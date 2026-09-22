export type MihomoInstanceId = string
export type ProxyMode = 'Global' | 'Rule' | 'Direct' | 'Script'

export type MihomoInstanceConfig = {
  id: MihomoInstanceId
  name: string
}

export type ManagedInstanceStatus = {
  id: string
  name: string
  running: boolean
  pid: number | null
  exitCode: number | null
  startedAt: string | null
  controllerUrl: string
  logs?: string[]
}

export type ManagedInstancesResponse = {
  instances: ManagedInstanceStatus[]
}

export type ProfileSummary = {
  id: string
  name: string
  sourceType: 'local' | 'subscription'
  updatedAt?: string | null
}

export type ProfilesResponse = {
  profiles: ProfileSummary[]
}

export type CoreSummary = {
  id: string
  name: string
  profileId: string
  controllerPort: number
  mixedPort: number
  mode: ProxyMode
  tunEnabled: boolean
  allowLan: boolean
  ipv6: boolean
  logLevel: string
}

export type CoresResponse = { cores: CoreSummary[] }
export type CoreMutationResponse = { core: CoreSummary; instances: ManagedInstanceStatus[] }
export type SystemProxyStatus = { enabled: boolean; server: string; coreId: string | null }

export type ProfileMutationResponse = {
  profile: ProfileSummary
  instances: ManagedInstanceStatus[]
}

export type MihomoProxy = {
  name: string
  type: string
  udp?: boolean
  now?: string
  all?: string[]
  history?: Array<{
    time: string
    delay: number
  }>
}

export type MihomoProxiesResponse = {
  proxies: Record<string, MihomoProxy>
}

export type MihomoRule = {
  index: number
  type: string
  payload: string
  proxy: string
  size?: number
}

export type MihomoRulesResponse = {
  rules: MihomoRule[]
}

export type MihomoVersion = {
  meta?: boolean
  premium?: boolean
  version: string
}

export type MihomoDelayResponse = {
  delay: number
}

export type MihomoConnection = {
  id: string
  metadata?: {
    network?: string
    type?: string
    host?: string
    sniffHost?: string
    destinationIP?: string
    destinationPort?: string | number
    sourceIP?: string
    sourcePort?: string | number
    process?: string
    processPath?: string
  }
  chains?: string[]
  rule?: string
  rulePayload?: string
  upload?: number
  download?: number
  start?: string
}

export type MihomoConnectionsResponse = {
  connections: MihomoConnection[]
  uploadTotal?: number
  downloadTotal?: number
}
