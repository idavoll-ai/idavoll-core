import { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route, NavLink, Navigate, useLocation } from 'react-router-dom'
import { AgentsPage } from './pages/AgentsPage'
import { AgentDetailPage } from './pages/AgentDetailPage'
import { CreateAgentPage } from './pages/CreateAgentPage'
import { TopicsPage } from './pages/TopicsPage'
import { TopicDetailPage } from './pages/TopicDetailPage'

// Pages that occupy the full screen (no sidebar)
const FULL_SCREEN_ROUTES = ['/agents/new']

function Sidebar({ theme, onToggleTheme }: { theme: 'dark' | 'light'; onToggleTheme: () => void }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <div className="sidebar-logo-icon">V</div>
        <span className="sidebar-logo-text">Vingolf</span>
      </div>

      <nav className="sidebar-nav">
        <NavLink
          to="/agents"
          className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
            <circle cx="12" cy="8" r="4" />
            <path d="M4 20c0-4 3.6-7 8-7s8 3 8 7" />
          </svg>
          Agents
        </NavLink>

        <NavLink
          to="/topics"
          className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
            <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
          </svg>
          Topics
        </NavLink>
      </nav>

      <div className="sidebar-footer">
        <button
          className="btn btn-ghost btn-sm"
          onClick={onToggleTheme}
          style={{ width: '100%', justifyContent: 'flex-start', gap: 8 }}
        >
          {theme === 'dark' ? '☀️ 亮色模式' : '🌙 暗色模式'}
        </button>
      </div>
    </aside>
  )
}

function AppLayout({ theme, onToggleTheme }: { theme: 'dark' | 'light'; onToggleTheme: () => void }) {
  const location = useLocation()
  const isFullScreen = FULL_SCREEN_ROUTES.some(r => location.pathname.startsWith(r))

  if (isFullScreen) {
    return (
      <Routes>
        <Route path="/agents/new" element={<CreateAgentPage />} />
      </Routes>
    )
  }

  return (
    <div className="app-layout">
      <Sidebar theme={theme} onToggleTheme={onToggleTheme} />
      <main className="main-content">
        <Routes>
          <Route path="/" element={<Navigate to="/agents" replace />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/agents/new" element={<CreateAgentPage />} />
          <Route path="/agents/:agentId" element={<AgentDetailPage />} />
          <Route path="/topics" element={<TopicsPage />} />
          <Route path="/topics/:topicId" element={<TopicDetailPage />} />
        </Routes>
      </main>
    </div>
  )
}

export default function App() {
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    const stored = localStorage.getItem('vingolf-theme')
    if (stored === 'light' || stored === 'dark') return stored
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('vingolf-theme', theme)
  }, [theme])

  const toggle = () => setTheme(t => t === 'dark' ? 'light' : 'dark')

  return (
    <BrowserRouter>
      <AppLayout theme={theme} onToggleTheme={toggle} />
    </BrowserRouter>
  )
}
