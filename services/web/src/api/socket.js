import { io } from 'socket.io-client'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5000'

let socket = null

export function connectSocket(token) {
  if (socket) socket.disconnect()

  socket = io(`${API_URL}/feed`, {
    auth: token ? { token } : undefined,
    transports: ['websocket', 'polling'],
  })

  return socket
}

export function disconnectSocket() {
  if (socket) {
    socket.disconnect()
    socket = null
  }
}