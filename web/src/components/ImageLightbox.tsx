import { useCallback, useEffect, useRef, useState } from 'react'

export interface ImagePreviewDetail {
  /** 可直接加载的 URL（/api/fs/raw?... 或远程地址）。 */
  url: string
  /** 展示用的说明文字。 */
  alt?: string
  /** 原始引用（本地绝对路径等），用于「打开原图」「下载」。 */
  src?: string
}

/** 打开图片预览（任意组件可调用，不必层层传 props）。 */
export function openImagePreview(detail: ImagePreviewDetail) {
  window.dispatchEvent(
    new CustomEvent<ImagePreviewDetail>('openminis:image-preview', { detail }),
  )
}

const MIN_SCALE = 0.1
const MAX_SCALE = 12

/**
 * 图片预览灯箱：点聊天里的缩略图放大看原图。
 *
 * 挂在 App 上（而不是气泡内部）—— 气泡可能位于带 transform/overflow 的祖先里，
 * `position: fixed` 在那种容器内会退化成相对定位，浮层会被裁掉。
 *
 * 两种模式：
 * * ``fit``  —— 默认，缩放到窗口内（max-width/height: 100%）；
 * * ``zoom`` —— 按 ``自然尺寸 × scale`` 给出**显式像素宽高**。
 *
 * 为什么 zoom 不用 ``transform: scale()``：transform 不参与布局，放大后画布不会
 * 变大，`overflow: auto` 也就没有可滚动的区域，图会被裁掉。显式尺寸才能滚。
 *
 * 关闭：点背景 / ✕ / Esc。滚轮缩放，按钮 适应窗口 / ＋ / － / 1:1。
 */
export function ImageLightbox() {
  const [preview, setPreview] = useState<ImagePreviewDetail | null>(null)
  const [mode, setMode] = useState<'fit' | 'zoom'>('fit')
  const [scale, setScale] = useState(1)
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null)
  const stageRef = useRef<HTMLDivElement>(null)

  const close = useCallback(() => setPreview(null), [])

  useEffect(() => {
    const onOpen = (e: Event) => {
      const detail = (e as CustomEvent<ImagePreviewDetail>).detail
      if (detail?.url) {
        setMode('fit')
        setScale(1)
        setNatural(null)
        setPreview(detail)
      }
    }
    window.addEventListener('openminis:image-preview', onOpen)
    return () => window.removeEventListener('openminis:image-preview', onOpen)
  }, [])

  useEffect(() => {
    if (!preview) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [preview, close])

  /** 当前「适应窗口」的等效缩放比（用来让滚轮从 100% 视图平滑起步）。 */
  const fitRatio = useCallback(() => {
    const stage = stageRef.current
    if (!natural || !stage) return 1
    const box = stage.getBoundingClientRect()
    const availW = Math.max(1, box.width - 32)
    const availH = Math.max(1, box.height - 32)
    return Math.min(availW / natural.w, availH / natural.h, 1)
  }, [natural])

  const clamp = (v: number) =>
    Math.min(MAX_SCALE, Math.max(MIN_SCALE, Number(v.toFixed(3))))

  // 滚轮缩放要 preventDefault（阻止页面滚动），所以用非 passive 监听。
  useEffect(() => {
    const stage = stageRef.current
    if (!preview || !stage) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15
      setScale((s) => clamp((mode === 'fit' ? fitRatio() : s) * factor))
      setMode('zoom')
    }
    stage.addEventListener('wheel', onWheel, { passive: false })
    return () => stage.removeEventListener('wheel', onWheel)
  }, [preview, mode, fitRatio])

  if (!preview) return null

  const zoomStyle =
    mode === 'zoom' && natural
      ? { width: natural.w * scale, height: natural.h * scale }
      : undefined

  return (
    <div className="img-lightbox" onClick={close} role="dialog" aria-modal="true">
      <div className="img-lb-bar" onClick={(e) => e.stopPropagation()}>
        <span className="img-lb-name" title={preview.src ?? preview.url}>
          {preview.alt || '图片预览'}
        </span>
        <div className="img-lb-actions">
          <button
            onClick={() => {
              setMode('fit')
              setScale(1)
            }}
            title="缩放到窗口内"
          >
            适应
          </button>
          <button
            onClick={() => {
              setScale((s) => clamp((mode === 'fit' ? fitRatio() : s) * 1.25))
              setMode('zoom')
            }}
            title="放大"
          >
            ＋
          </button>
          <button
            onClick={() => {
              setScale((s) => clamp((mode === 'fit' ? fitRatio() : s) / 1.25))
              setMode('zoom')
            }}
            title="缩小"
          >
            －
          </button>
          <button
            onClick={() => {
              setScale(1)
              setMode('zoom')
            }}
            title="原始尺寸（100%）"
          >
            1:1
          </button>
          <a href={preview.url} target="_blank" rel="noreferrer" title="在新标签打开">
            打开原图
          </a>
          <a href={preview.url} download={preview.alt || undefined} title="下载">
            下载
          </a>
          <button onClick={close} title="关闭（Esc）">
            ✕
          </button>
        </div>
      </div>

      <div className="img-lb-stage" ref={stageRef}>
        <img
          key={preview.url}
          src={preview.url}
          alt={preview.alt || ''}
          style={zoomStyle}
          className={mode === 'fit' ? 'fit' : 'zoom'}
          onClick={(e) => e.stopPropagation()}
          onLoad={(e) =>
            setNatural({
              w: e.currentTarget.naturalWidth,
              h: e.currentTarget.naturalHeight,
            })
          }
        />
      </div>

      <div className="img-lb-hint">
        {mode === 'fit'
          ? '滚轮缩放 · 点背景或 Esc 关闭'
          : `${Math.round(scale * 100)}% · 滚轮缩放 · 拖动滚动条查看 · Esc 关闭`}
      </div>
    </div>
  )
}
