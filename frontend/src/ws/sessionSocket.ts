import {
  WS_CLOSE_AUTH_REQUIRED,
  WS_CLOSE_SERVER_BUSY,
  WS_CLOSE_SESSION_UNAVAILABLE,
  type AuthMessage,
  type ClientMessage,
  type ServerMessage,
} from '../types/ws'
import { appUrl } from '../lib/basePath'

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface SessionSocketHandlers {
  onMessage: (message: ServerMessage) => void
  onStatus: (status: ConnectionStatus) => void
  /** Called on every (re)connect — resend any client state the server should know. */
  onOpen: () => void
  /** The server no longer has this session (ended or unknown); don't retry it. */
  onSessionUnavailable: () => void
  /** The server is at capacity; called once per run of refusals while it keeps retrying. */
  onServerBusy: () => void
}

const MIN_BACKOFF_MS = 500
const MAX_BACKOFF_MS = 10_000

/**
 * A WebSocket to one mission session with exponential-backoff reconnects. Every connection
 * starts with an `auth` message carrying the session's token (never put in the URL).
 */
export class SessionSocket {
  private ws: WebSocket | null = null
  private backoffMs = MIN_BACKOFF_MS
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private closedByClient = false
  private busy = false
  private readonly sessionId: number
  private readonly token: string
  private readonly handlers: SessionSocketHandlers

  constructor(sessionId: number, token: string, handlers: SessionSocketHandlers) {
    this.sessionId = sessionId
    this.token = token
    this.handlers = handlers
  }

  connect(): void {
    this.closedByClient = false
    this.open('connecting')
  }

  send(message: ClientMessage): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false
    this.ws.send(JSON.stringify(message))
    return true
  }

  close(): void {
    this.closedByClient = true
    if (this.retryTimer) clearTimeout(this.retryTimer)
    this.ws?.close()
    this.ws = null
    this.handlers.onStatus('closed')
  }

  private open(status: ConnectionStatus): void {
    this.handlers.onStatus(status)
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${scheme}://${window.location.host}${appUrl(`/ws/sessions/${this.sessionId}`)}`)
    this.ws = ws

    ws.onopen = () => {
      const auth: AuthMessage = { type: 'auth', token: this.token }
      ws.send(JSON.stringify(auth))
      this.handlers.onStatus('open')
      this.handlers.onOpen()
    }
    ws.onmessage = (event: MessageEvent<string>) => {
      const message = JSON.parse(event.data) as ServerMessage
      if (message.type !== 'error') {
        // Authenticated and streaming (a refusal arrives as an error, then a close): the
        // next drop starts over with short retries.
        this.backoffMs = MIN_BACKOFF_MS
        this.busy = false
      }
      this.handlers.onMessage(message)
    }
    ws.onclose = (event) => {
      if (this.ws !== ws || this.closedByClient) return
      this.ws = null
      if (event.code === WS_CLOSE_SESSION_UNAVAILABLE || event.code === WS_CLOSE_AUTH_REQUIRED) {
        this.handlers.onStatus('closed')
        this.handlers.onSessionUnavailable()
        return
      }
      if (event.code === WS_CLOSE_SERVER_BUSY && !this.busy) {
        this.busy = true
        this.handlers.onServerBusy()
      }
      this.handlers.onStatus('reconnecting')
      this.retryTimer = setTimeout(() => this.open('reconnecting'), this.backoffMs)
      this.backoffMs = Math.min(this.backoffMs * 2, MAX_BACKOFF_MS)
    }
  }
}
