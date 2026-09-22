import type {
  MihomoDelayResponse,
  MihomoConnectionsResponse,
  MihomoInstanceConfig,
  MihomoProxiesResponse,
  MihomoRulesResponse,
  MihomoVersion,
  ManagedInstancesResponse,
  ProfileMutationResponse,
  ProfileSummary,
  ProfilesResponse,
  CoresResponse,
  CoreSummary,
  CoreMutationResponse,
  SystemProxyStatus,
  ProxyMode,
} from '../types/mihomo'

export class MihomoApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'MihomoApiError'
    this.status = status
  }
}

export class MihomoClient {
  private readonly backendUrl: string
  private readonly instanceId: string

  constructor(config: MihomoInstanceConfig, backendUrl = 'http://127.0.0.1:17890') {
    this.backendUrl = backendUrl.replace(/\/$/, '')
    this.instanceId = encodeURIComponent(config.id)
  }

  async getVersion(): Promise<MihomoVersion> {
    return this.request<MihomoVersion>('/version')
  }

  async getProxies(): Promise<MihomoProxiesResponse> {
    return this.request<MihomoProxiesResponse>('/proxies')
  }

  async getRules(): Promise<MihomoRulesResponse> {
    return this.request<MihomoRulesResponse>('/rules')
  }

  async getConnections(): Promise<MihomoConnectionsResponse> {
    return this.request<MihomoConnectionsResponse>('/connections')
  }

  async closeConnection(connectionId: string): Promise<void> {
    await this.request(`/connections/${encodeURIComponent(connectionId)}`, { method: 'DELETE' })
  }

  async closeAllConnections(): Promise<void> {
    await this.request('/connections', { method: 'DELETE' })
  }

  async clearRuntimeLogs(): Promise<void> {
    await this.request('/runtime-logs', { method: 'DELETE' })
  }

  async getRuntimeLogs(): Promise<string[]> {
    const response = await this.request<{ logs: string[] }>('/runtime-logs')
    return response.logs
  }

  async selectProxy(groupName: string, proxyName: string): Promise<void> {
    await this.request(`/proxies/${encodeURIComponent(groupName)}`, {
      method: 'PUT',
      body: JSON.stringify({ name: proxyName }),
    })
  }

  async checkProxy(proxyName: string, url = 'https://www.gstatic.com/generate_204', timeout = 5000, signal?: AbortSignal): Promise<number> {
    const query = new URLSearchParams({
      url,
      timeout: String(timeout),
    })
    const result = await this.request<MihomoDelayResponse>(`/proxies/${encodeURIComponent(proxyName)}/delay?${query}`, { signal })
    return result.delay
  }

  async checkGroup(groupName: string, url = 'https://www.gstatic.com/generate_204', timeout = 5000): Promise<Record<string, number>> {
    const query = new URLSearchParams({
      url,
      timeout: String(timeout),
    })
    return this.request<Record<string, number>>(`/group/${encodeURIComponent(groupName)}/delay?${query}`)
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers)
    headers.set('Accept', 'application/json')
    if (init.body) headers.set('Content-Type', 'application/json')

    const response = await fetch(`${this.backendUrl}/api/instances/${this.instanceId}${path}`, {
      ...init,
      headers,
    })
    if (!response.ok) {
      const detail = await response.text().catch(() => '')
      throw new MihomoApiError(detail || `Mihomo API request failed: ${response.status}`, response.status)
    }
    if (response.status === 204) return undefined as T
    return response.json() as Promise<T>
  }
}

export async function listManagedInstances(backendUrl = 'http://127.0.0.1:17890'): Promise<ManagedInstancesResponse> {
  const response = await fetch(`${backendUrl.replace(/\/$/, '')}/api/instances`, {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new MihomoApiError(detail || `OhMyClash backend request failed: ${response.status}`, response.status)
  }
  return response.json() as Promise<ManagedInstancesResponse>
}

export async function listProfiles(backendUrl = 'http://127.0.0.1:17890'): Promise<ProfilesResponse> {
  return requestBackend<ProfilesResponse>('/api/profiles', backendUrl)
}

export async function listCores(backendUrl = 'http://127.0.0.1:17890'): Promise<CoresResponse> {
  return requestBackend<CoresResponse>('/api/cores', backendUrl)
}

export async function createCore(name: string, profileId: string, backendUrl = 'http://127.0.0.1:17890'): Promise<CoreMutationResponse> {
  return requestBackend<CoreMutationResponse>('/api/cores', backendUrl, { method: 'POST', body: JSON.stringify({ name, profileId }) })
}

export async function updateCore(coreId: string, changes: Record<string, unknown>, backendUrl = 'http://127.0.0.1:17890'): Promise<CoreMutationResponse> {
  return requestBackend<CoreMutationResponse>(`/api/cores/${encodeURIComponent(coreId)}`, backendUrl, { method: 'PUT', body: JSON.stringify(changes) })
}

export async function updateCoreMode(coreId: string, mode: ProxyMode, backendUrl = 'http://127.0.0.1:17890'): Promise<{ core: CoreSummary }> {
  return requestBackend<{ core: CoreSummary }>(`/api/cores/${encodeURIComponent(coreId)}/mode`, backendUrl, {
    method: 'PUT',
    body: JSON.stringify({ mode }),
  })
}

export async function deleteCore(coreId: string, backendUrl = 'http://127.0.0.1:17890'): Promise<CoreMutationResponse> {
  return requestBackend<CoreMutationResponse>(`/api/cores/${encodeURIComponent(coreId)}`, backendUrl, { method: 'DELETE' })
}

export async function getSystemProxy(backendUrl = 'http://127.0.0.1:17890'): Promise<SystemProxyStatus> {
  return requestBackend<SystemProxyStatus>('/api/system-proxy', backendUrl)
}

export async function setSystemProxy(coreId: string, enabled: boolean, backendUrl = 'http://127.0.0.1:17890'): Promise<SystemProxyStatus> {
  return requestBackend<SystemProxyStatus>('/api/system-proxy', backendUrl, { method: 'PUT', body: JSON.stringify({ coreId, enabled }) })
}

export async function importProfile(name: string, content: string, backendUrl = 'http://127.0.0.1:17890'): Promise<ProfileMutationResponse> {
  return requestBackend<ProfileMutationResponse>('/api/profiles/import', backendUrl, {
    method: 'POST',
    body: JSON.stringify({ name, content }),
  })
}

export async function addSubscription(name: string, url: string, backendUrl = 'http://127.0.0.1:17890'): Promise<ProfileMutationResponse> {
  return requestBackend<ProfileMutationResponse>('/api/profiles/subscribe', backendUrl, {
    method: 'POST',
    body: JSON.stringify({ name, url }),
  })
}

export async function refreshProfile(profileId: string, backendUrl = 'http://127.0.0.1:17890'): Promise<ProfileMutationResponse> {
  return requestBackend<ProfileMutationResponse>(`/api/profiles/${encodeURIComponent(profileId)}/refresh`, backendUrl, {
    method: 'POST',
  })
}

export async function getProfileContent(profileId: string, backendUrl = 'http://127.0.0.1:17890'): Promise<{ profile: ProfileSummary; content: string }> {
  return requestBackend(`/api/profiles/${encodeURIComponent(profileId)}/content`, backendUrl)
}

export async function saveProfile(profileId: string, name: string, content: string, backendUrl = 'http://127.0.0.1:17890'): Promise<ProfileMutationResponse> {
  return requestBackend(`/api/profiles/${encodeURIComponent(profileId)}`, backendUrl, { method: 'PUT', body: JSON.stringify({ name, content }) })
}

export async function deleteProfile(profileId: string, backendUrl = 'http://127.0.0.1:17890'): Promise<{ profile: ProfileSummary }> {
  return requestBackend(`/api/profiles/${encodeURIComponent(profileId)}`, backendUrl, { method: 'DELETE' })
}

export async function revealProfile(profileId: string, backendUrl = 'http://127.0.0.1:17890'): Promise<void> {
  await requestBackend(`/api/profiles/${encodeURIComponent(profileId)}/reveal`, backendUrl, { method: 'POST' })
}

async function requestBackend<T>(path: string, backendUrl: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${backendUrl.replace(/\/$/, '')}${path}`, { ...init, headers })
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new MihomoApiError(detail || `OhMyClash backend request failed: ${response.status}`, response.status)
  }
  return response.json() as Promise<T>
}
