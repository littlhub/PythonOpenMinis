/**
 * 麦克风录音 → 16 kHz 单声道 WAV。
 *
 * 为什么不用 ``MediaRecorder``：它只能吐 webm/ogg（Opus），而服务端的离线识别
 * 引擎只原生解析 WAV，其余容器要借 ffmpeg 转码 —— 机器上没装 ffmpeg 时「🎤」
 * 就直接 503。用 WebAudio 抓 PCM 再自己封 WAV，前后端都不依赖外部转码器，
 * 而且 16 kHz 单声道正好是识别引擎的输入格式，省掉一次重采样。
 */

/** 识别引擎的输入采样率（见 ``speech/stt_core.py`` 的 ``SAMPLE_RATE``）。 */
const TARGET_RATE = 16000

/** 太短的片段几乎肯定是误触，识别出来只有噪声。 */
export const MIN_RECORD_MS = 300

/** 取源窗口均值当低通：48k→16k 正好是三取一，避免直接抽样带来的混叠。 */
function downsample(input: Float32Array, from: number, to: number): Float32Array {
  if (from === to || input.length === 0) return input
  const ratio = from / to
  const length = Math.max(1, Math.round(input.length / ratio))
  const out = new Float32Array(length)
  for (let i = 0; i < length; i++) {
    const start = Math.floor(i * ratio)
    const end = Math.min(input.length, Math.floor((i + 1) * ratio))
    let sum = 0
    let count = 0
    for (let j = start; j < end; j++) {
      sum += input[j]
      count++
    }
    out[i] = count > 0 ? sum / count : 0
  }
  return out
}

function writeAscii(view: DataView, offset: number, text: string): void {
  for (let i = 0; i < text.length; i++) {
    view.setUint8(offset + i, text.charCodeAt(i))
  }
}

/** 把 float32 采样封成 16-bit PCM WAV（RIFF 头 44 字节）。 */
function encodeWav(chunks: Float32Array[], sourceRate: number): Blob {
  let total = 0
  for (const chunk of chunks) total += chunk.length

  const merged = new Float32Array(total)
  let offset = 0
  for (const chunk of chunks) {
    merged.set(chunk, offset)
    offset += chunk.length
  }

  const samples = downsample(merged, sourceRate, TARGET_RATE)
  const pcm = new Int16Array(samples.length)
  for (let i = 0; i < samples.length; i++) {
    const sample = Math.max(-1, Math.min(1, samples[i]))
    pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff
  }

  const header = new ArrayBuffer(44)
  const view = new DataView(header)
  writeAscii(view, 0, 'RIFF')
  view.setUint32(4, 36 + pcm.byteLength, true)
  writeAscii(view, 8, 'WAVE')
  writeAscii(view, 12, 'fmt ')
  view.setUint32(16, 16, true) // fmt 块长度
  view.setUint16(20, 1, true) // PCM
  view.setUint16(22, 1, true) // 单声道
  view.setUint32(24, TARGET_RATE, true)
  view.setUint32(28, TARGET_RATE * 2, true) // 字节率
  view.setUint16(32, 2, true) // 块对齐
  view.setUint16(34, 16, true) // 位深
  writeAscii(view, 36, 'data')
  view.setUint32(40, pcm.byteLength, true)

  return new Blob([header, pcm.buffer], { type: 'audio/wav' })
}

export interface RecordedClip {
  blob: Blob
  durationMs: number
}

/**
 * 一次性的录音会话：``start()`` 开始抓 PCM，``stop()`` 收尾并给出 WAV。
 *
 * ``ScriptProcessorNode`` 虽然被标记为废弃，但所有 Chromium/Firefox 都还支持，
 * 且不需要额外加载 AudioWorklet 模块文件（Vite 下要多一份产物，得不偿失）。
 */
export class WavRecorder {
  private ctx: AudioContext | null = null
  private stream: MediaStream | null = null
  private processor: ScriptProcessorNode | null = null
  private sink: GainNode | null = null
  private chunks: Float32Array[] = []
  private sampleRate = TARGET_RATE

  async start(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('当前环境不支持录音（需要 getUserMedia）')
    }

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        // 回声消除/降噪对语音识别是加分项，浏览器不支持时会自动忽略。
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    })

    const Ctor = window.AudioContext ?? (window as unknown as {
      webkitAudioContext?: typeof AudioContext
    }).webkitAudioContext
    if (!Ctor) {
      stream.getTracks().forEach((t) => t.stop())
      throw new Error('当前环境不支持 WebAudio')
    }

    const ctx = new Ctor()
    const source = ctx.createMediaStreamSource(stream)
    // 4096 ≈ 48k 下的 85ms 一块，延迟够低又不会频繁触发回调。
    const processor = ctx.createScriptProcessor(4096, 1, 1)
    const sink = ctx.createGain()
    sink.gain.value = 0 // 不接到扬声器，避免啸叫

    processor.onaudioprocess = (event) => {
      // 复制一份：这个 buffer 会被浏览器复用，直接存引用会拿到被覆盖的数据。
      this.chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)))
    }

    source.connect(processor)
    processor.connect(sink)
    sink.connect(ctx.destination)

    this.ctx = ctx
    this.stream = stream
    this.processor = processor
    this.sink = sink
    this.chunks = []
    this.sampleRate = ctx.sampleRate
  }

  /** 停止录音并返回 WAV。没录到内容时返回 ``null``。 */
  stop(): RecordedClip | null {
    const chunks = this.chunks
    const rate = this.sampleRate
    this.release()

    if (chunks.length === 0) return null
    let frames = 0
    for (const chunk of chunks) frames += chunk.length
    if (frames === 0) return null

    return {
      blob: encodeWav(chunks, rate),
      durationMs: Math.round((frames / rate) * 1000),
    }
  }

  /** 放弃这次录音（组件卸载、切会话时调用）。 */
  cancel(): void {
    this.release()
  }

  private release(): void {
    this.chunks = []
    try {
      this.processor?.disconnect()
      this.sink?.disconnect()
    } catch {
      /* 节点已断开 */
    }
    this.processor = null
    this.sink = null
    // 关掉麦克风轨道，否则浏览器标签页会一直亮着录音指示灯。
    this.stream?.getTracks().forEach((track) => track.stop())
    this.stream = null
    const ctx = this.ctx
    this.ctx = null
    if (ctx && ctx.state !== 'closed') {
      void ctx.close().catch(() => undefined)
    }
  }
}
