import { useState } from 'react'

/**
 * SchedulerView - 定时任务管理页面
 * 创建、查看和管理定时任务与提醒
 */
export function SchedulerView() {
  const [tasks, setTasks] = useState([
    { id: 1, name: '每日早报', type: 'cron', schedule: '0 8 * * *', nextRun: '明天 08:00', status: 'active' },
    { id: 2, name: '每周报告', type: 'cron', schedule: '0 9 * * 1', nextRun: '下周一 09:00', status: 'active' },
    { id: 3, name: '会议提醒', type: 'once', schedule: '+30m', nextRun: '30分钟后', status: 'pending' },
    { id: 4, name: '数据备份', type: 'interval', schedule: '每6小时', nextRun: '6小时后', status: 'active' },
  ])

  const [showCreate, setShowCreate] = useState(false)
  const [newTask, setNewTask] = useState({ name: '', type: 'cron', schedule: '' })

  const createTask = () => {
    if (!newTask.name.trim()) return
    const task = {
      id: Date.now(),
      name: newTask.name,
      type: newTask.type,
      schedule: newTask.schedule,
      nextRun: '待计算',
      status: 'active',
    }
    setTasks([...tasks, task])
    setNewTask({ name: '', type: 'cron', schedule: '' })
    setShowCreate(false)
  }

  const deleteTask = (id: number) => {
    setTasks(tasks.filter((t) => t.id !== id))
  }

  const toggleTask = (id: number) => {
    setTasks(tasks.map((t) => t.id === id ? { ...t, status: t.status === 'active' ? 'paused' : 'active' } : t))
  }

  return (
    <div className="pane">
      <div className="pane-card">
        <div className="kb-header">
          <h2>⏰ 定时任务</h2>
          <span className="kb-count">{tasks.filter((t) => t.status === 'active').length} 个运行中</span>
        </div>

        {/* 创建新任务 */}
        {!showCreate ? (
          <button className="memory-add-btn" onClick={() => setShowCreate(true)}>
            ＋ 新建任务
          </button>
        ) : (
          <div className="create-task-form">
            <h3>新建定时任务</h3>
            <div className="form-group">
              <label>任务名称</label>
              <input
                type="text"
                placeholder="输入任务名称..."
                value={newTask.name}
                onChange={(e) => setNewTask({ ...newTask, name: e.target.value })}
              />
            </div>
            <div className="form-group">
              <label>任务类型</label>
              <select
                value={newTask.type}
                onChange={(e) => setNewTask({ ...newTask, type: e.target.value })}
              >
                <option value="cron">Cron 表达式</option>
                <option value="once">一次性任务</option>
                <option value="interval">间隔任务</option>
              </select>
            </div>
            <div className="form-group">
              <label>调度时间</label>
              <input
                type="text"
                placeholder="例: 0 8 * * * 或 +30m 或 每小时"
                value={newTask.schedule}
                onChange={(e) => setNewTask({ ...newTask, schedule: e.target.value })}
              />
            </div>
            <div className="form-actions">
              <button className="btn-cancel" onClick={() => setShowCreate(false)}>取消</button>
              <button className="btn-create" onClick={createTask}>创建</button>
            </div>
          </div>
        )}

        {/* 任务列表 */}
        <div className="tasks-list">
          {tasks.length === 0 ? (
            <div className="kb-empty">暂无定时任务</div>
          ) : (
            tasks.map((task) => (
              <div key={task.id} className={`task-card ${task.status}`}>
                <div className="task-info">
                  <div className="task-name">{task.name}</div>
                  <div className="task-meta">
                    <span className="task-type">{getTaskTypeLabel(task.type)}</span>
                    <span className="task-schedule">{task.schedule}</span>
                    <span className="task-next">下次运行: {task.nextRun}</span>
                  </div>
                </div>
                <div className="task-actions">
                  <button
                    className={`task-toggle ${task.status === 'active' ? 'active' : ''}`}
                    onClick={() => toggleTask(task.id)}
                  >
                    {task.status === 'active' ? '暂停' : '启用'}
                  </button>
                  <button className="task-delete" onClick={() => deleteTask(task.id)}>
                    删除
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}

function getTaskTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    cron: 'Cron',
    once: '一次性',
    interval: '间隔',
  }
  return labels[type] || type
}
