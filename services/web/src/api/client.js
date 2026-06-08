const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5000'

export function getTokens() {
  return {
    access: localStorage.getItem('access_token'),
    refresh: localStorage.getItem('refresh_token'),
  }
}

export function setTokens(access, refresh) {
  if (access) localStorage.setItem('access_token', access)
  if (refresh) localStorage.setItem('refresh_token', refresh)
}

export function clearTokens() {
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
}

async function refreshAccessToken() {
  const { refresh } = getTokens()
  if (!refresh) return null

  const res = await fetch(`${API_URL}/auth/refresh`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${refresh}`,
      'Content-Type': 'application/json',
    },
  })

  if (!res.ok) {
    clearTokens()
    return null
  }

  const data = await res.json()
  setTokens(data.access_token, null)
  return data.access_token
}

export async function api(path, options = {}) {
  const { access } = getTokens()
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  }
  if (access) headers['Authorization'] = `Bearer ${access}`

  let res = await fetch(`${API_URL}${path}`, { ...options, headers })

  if (res.status === 401 && access) {
    const newToken = await refreshAccessToken()
    if (newToken) {
      headers['Authorization'] = `Bearer ${newToken}`
      res = await fetch(`${API_URL}${path}`, { ...options, headers })
    }
  }

  return res
}