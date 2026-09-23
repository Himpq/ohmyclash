<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import YamlEditor from './components/YamlEditor.vue'
import { addSubscription, createCore, deleteCore, deleteProfile, getProfileContent, getSystemProxy, importProfile, listCores, listManagedInstances, listProfiles, MihomoClient, refreshProfile, revealProfile, saveProfile, setSystemProxy, updateCore, updateCoreMode } from './services/mihomo'
import type { CoreSummary, ManagedInstanceStatus, MihomoConnection, MihomoInstanceConfig, MihomoInstanceId, MihomoProxy, MihomoRule, ProfileSummary, ProxyMode } from './types/mihomo'

type PageId = 'general' | 'proxies' | 'profiles' | 'cores' | 'logs' | 'connections' | 'settings' | 'feedback'
type LogLevel = 'INFO' | 'WARN' | 'ERROR'
type ProxySortMode = 'natural' | 'delay'

const storedProxySortMode = localStorage.getItem('ohmyclash.proxySortMode')

type ProxyNode = {
  name: string
  type: string
  badge: string
  delay?: number
  testError?: string
}

type ProxyGroup = {
  name: string
  current: string
  items: ProxyNode[]
}

type CoreDraft = {
  name: string
  controllerPort: string
  mixedPort: string
}

type LogEntry = {
  time: string
  level: LogLevel
  message: string
}

type Connection = {
  id: string
  host: string
  address: string
  network: string
  inbound: string
  process: string
  policy: string
  startedAt: number
  upload: number
  download: number
  uploadSpeed: number
  downloadSpeed: number
}

type ConnectionSort = 'uploadSpeed' | 'downloadSpeed' | 'upload' | 'download' | 'time'
type DeleteTarget = { kind: 'profile' | 'core'; id: string; name: string }

const pageIds: PageId[] = ['general', 'proxies', 'profiles', 'cores', 'logs', 'connections', 'settings', 'feedback']
const storedPage = localStorage.getItem('ohmyclash.activePage') as PageId | null
const activePage = ref<PageId>(storedPage && pageIds.includes(storedPage) ? storedPage : 'general')
const allowLanEnabled = ref(true)
const ipv6Enabled = ref(false)
const tunEnabled = ref(false)
const systemProxyCoreId = ref<string | null>(null)
const systemProxyPendingCoreId = ref<string | null>(null)
const startWithWindowsEnabled = ref(false)
const rememberPageEnabled = ref(localStorage.getItem('ohmyclash.rememberPage') !== 'false')
const trayOnCloseEnabled = ref(localStorage.getItem('ohmyclash.trayResident') === 'true')
const proxyMode = ref<ProxyMode>('Rule')
const proxyModePending = ref(false)
const proxySortMode = ref<ProxySortMode>(storedProxySortMode === 'delay' ? 'delay' : 'natural')
const selectedProxy = ref('VPS-CF')
const checkingProxies = ref<Record<string, true>>({})
const testingProxyGroups = ref<Record<string, true>>({})
const collapsedProxyGroups = ref<Record<string, boolean>>({})
const proxyTestConcurrency = 3
const proxyTestTimeout = 3000
let proxyTestGeneration = 0
let proxyTestAbortController: AbortController | null = null
let proxyLoadRequestId = 0
let proxyLoadingInstanceId = ''
const activeInstanceId = ref<MihomoInstanceId>(localStorage.getItem('ohmyclash.activeInstanceId') ?? '')
const backendStatus = ref<'idle' | 'loading' | 'connected' | 'error'>('idle')
const backendError = ref('')
const proxyDataLoaded = ref(false)
const selectedProfile = ref(localStorage.getItem('ohmyclash.selectedProfile') ?? '')
const showInstanceMenu = ref(false)
const updatingProfile = ref('')
const switchingProfileId = ref('')
const coreMutationPending = ref(false)
const profiles = ref<ProfileSummary[]>([])
const cores = ref<CoreSummary[]>([])
const coreDrafts = ref<Record<string, CoreDraft>>({})
const showCoreDialog = ref(false)
const coreName = ref('')
const coreProfileId = ref('')
const coreCreationPending = ref(false)
const editingProfileId = ref('')
const editingProfileName = ref('')
const editingProfileContent = ref('')
const profileStatus = ref<'idle' | 'loading' | 'error'>('idle')
const profileError = ref('')
const profileFileInput = ref<HTMLInputElement | null>(null)
const showSubscriptionDialog = ref(false)
const subscriptionName = ref('')
const subscriptionUrl = ref('')
const showErrorsOnly = ref(false)
const logPanel = ref<HTMLElement | null>(null)
const connectionsPanel = ref<HTMLElement | null>(null)
const logStickToBottom = ref(true)
const connectionSearch = ref('')
const connectionsPaused = ref(false)
const connectionSort = ref<ConnectionSort>('downloadSpeed')
const connectionScrollTop = ref(0)
const connectionViewportHeight = ref(720)
const connectionRowHeight = 61
const connectionVirtualOverscan = 8
const uploadTotal = ref(0)
const downloadTotal = ref(0)
const uploadRate = ref(0)
const downloadRate = ref(0)
const toastMessage = ref('')
const contextMenu = ref<{ kind: 'profile' | 'core'; id: string; x: number; y: number } | null>(null)
const deleteTarget = ref<DeleteTarget | null>(null)
const deletingTarget = ref(false)
const connectionSamples = new Map<string, { upload: number; download: number; sampledAt: number }>()
let connectionsLoading = false
let logsLoading = false
let statusLoading = false
let connectionLoadGeneration = 0
let connectionLoadRequestId = 0

const availableInstances = ref<MihomoInstanceConfig[]>([])
const managedInstances = ref<ManagedInstanceStatus[]>([])
const uptimeClock = ref(Date.now())

watch(proxySortMode, (value) => localStorage.setItem('ohmyclash.proxySortMode', value))
watch(activeInstanceId, (value) => localStorage.setItem('ohmyclash.activeInstanceId', value))
watch(selectedProfile, (value) => localStorage.setItem('ohmyclash.selectedProfile', value))
watch(activePage, (value) => {
  if (rememberPageEnabled.value) localStorage.setItem('ohmyclash.activePage', value)
})

function getMihomoClient(instanceId: MihomoInstanceId) {
  const config = availableInstances.value.find((item) => item.id === instanceId)
  if (!config) throw new Error(`managed instance not found: ${instanceId}`)
  return new MihomoClient(config)
}

const activeInstance = computed(() => availableInstances.value.find((item) => item.id === activeInstanceId.value) ?? { id: activeInstanceId.value, name: '未选择配置' })
const activeCore = computed(() => cores.value.find((core) => core.id === activeInstanceId.value))
const activeProfileId = computed(() => activeCore.value?.profileId ?? '')
const backendStatusLabel = computed(() => {
  if (backendStatus.value === 'loading') return '正在连接核心'
  if (backendStatus.value === 'connected') return '核心已连接'
  if (backendStatus.value === 'error') return '核心连接失败'
  return '尚未连接核心'
})
const connectionStatusLabel = computed(() => {
  if (backendStatus.value === 'connected') return '核心已连接'
  if (backendStatus.value === 'loading') return '正在连接'
  if (backendStatus.value === 'error') return '核心未连接'
  return '等待代理配置'
})

const coreUptime = computed(() => {
  const instance = managedInstances.value.find((item) => item.id === activeInstanceId.value)
  if (!instance?.running || !instance.startedAt) return '00 : 00 : 00'
  const startedAt = new Date(instance.startedAt).getTime()
  if (!Number.isFinite(startedAt)) return '00 : 00 : 00'
  const totalSeconds = Math.max(0, Math.floor((uptimeClock.value - startedAt) / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, '0')).join(' : ')
})

const proxyModes: Array<{ id: ProxyMode; label: string }> = [
  { id: 'Global', label: 'Global' },
  { id: 'Rule', label: 'Rule' },
  { id: 'Direct', label: 'Direct' },
  { id: 'Script', label: 'Script' },
]

const proxyNodes = ref<ProxyNode[]>([])
const ruleProxyGroups = ref<ProxyGroup[]>([])

function proxyDelayRank(delay?: number) {
  return typeof delay === 'number' && Number.isFinite(delay) && delay >= 0 ? delay : Number.MAX_SAFE_INTEGER
}

function sortProxyItems(items: ProxyNode[]) {
  if (proxySortMode.value !== 'delay') return items
  return items
    .map((item, index) => ({ item, index }))
    .sort((left, right) => proxyDelayRank(left.item.delay) - proxyDelayRank(right.item.delay) || left.index - right.index)
    .map(({ item }) => item)
}

const visibleProxyGroups = computed<ProxyGroup[]>(() => {
  if (!proxyDataLoaded.value) return []
  if (proxyMode.value === 'Rule') return ruleProxyGroups.value.map((group) => ({ ...group, items: sortProxyItems(group.items) }))
  if (proxyMode.value === 'Direct') {
    return [{ name: 'DIRECT', current: 'DIRECT', items: [{ name: 'DIRECT', type: 'Direct', badge: 'UDP' }] }]
  }
  if (proxyMode.value === 'Script') {
    return [{ name: 'SCRIPT', current: 'DIRECT', items: [{ name: 'DIRECT', type: 'Direct', badge: 'UDP' }] }]
  }
  return [{ name: 'GLOBAL', current: selectedProxy.value, items: sortProxyItems(proxyNodes.value) }]
})

function mapMihomoProxy(name: string, proxy?: MihomoProxy): ProxyNode {
  const history = proxy?.history ?? []
  const lastHistory = history.length > 0 ? history[history.length - 1] : undefined
  const delay = lastHistory?.delay
  const hasValidDelay = typeof delay === 'number' && Number.isFinite(delay) && delay > 0
  return {
    name,
    type: proxy?.type ?? 'Unknown',
    badge: proxy?.udp ? 'UDP' : '',
    delay: hasValidDelay ? delay : undefined,
    testError: typeof delay === 'number' && !hasValidDelay ? 'Timeout' : undefined,
  }
}

function buildRuleProxyGroups(proxies: Record<string, MihomoProxy>, rules: MihomoRule[]): ProxyGroup[] {
  const orderedGroupNames: string[] = []
  for (const rule of rules) {
    if (!orderedGroupNames.includes(rule.proxy)) orderedGroupNames.push(rule.proxy)
  }

  return orderedGroupNames.flatMap((groupName) => {
    const group = proxies[groupName]
    if (!group?.all?.length) return []
    return [{
      name: groupName,
      current: group.now ?? group.all[0],
      items: group.all.map((proxyName) => mapMihomoProxy(proxyName, proxies[proxyName])),
    }]
  })
}

function applyMihomoSnapshot(proxyResponse: { proxies: Record<string, MihomoProxy> }, ruleResponse: { rules: MihomoRule[] }) {
  const proxies = proxyResponse.proxies
  const globalProxy = proxies.GLOBAL
  const globalNames = globalProxy?.all ?? Object.keys(proxies).filter((name) => name !== 'GLOBAL')
  proxyNodes.value = globalNames.map((name) => mapMihomoProxy(name, proxies[name]))
  ruleProxyGroups.value = buildRuleProxyGroups(proxies, ruleResponse.rules)
  if (globalProxy?.now) selectedProxy.value = globalProxy.now
}

const logs = ref<LogEntry[]>([])
const connections = ref<Connection[]>([])

watch(logs, async () => {
  if (!logStickToBottom.value) return
  await nextTick()
  if (logPanel.value) logPanel.value.scrollTop = logPanel.value.scrollHeight
}, { deep: true })

const visibleLogs = computed(() => {
  if (!showErrorsOnly.value) return logs.value
  return logs.value.filter((entry) => entry.level === 'WARN' || entry.level === 'ERROR')
})

const visibleConnections = computed(() => {
  const keyword = connectionSearch.value.trim().toLowerCase()
  const filtered = keyword ? connections.value.filter((connection) =>
    [connection.host, connection.address, connection.process, connection.policy, connection.network, connection.inbound].some((value) => value.toLowerCase().includes(keyword)),
  ) : connections.value
  return filtered
    .map((connection, index) => ({ connection, index }))
    .sort((left, right) => {
      if (connectionSort.value === 'time') return left.connection.startedAt - right.connection.startedAt
      const leftValue = finiteConnectionMetric(left.connection[connectionSort.value])
      const rightValue = finiteConnectionMetric(right.connection[connectionSort.value])
      return rightValue - leftValue || left.index - right.index
    })
    .map(({ connection }) => connection)
})

const virtualConnectionStart = computed(() => Math.min(
  Math.max(0, visibleConnections.value.length - Math.ceil(connectionViewportHeight.value / connectionRowHeight)),
  Math.max(0, Math.floor(connectionScrollTop.value / connectionRowHeight) - connectionVirtualOverscan),
))
const virtualConnectionEnd = computed(() => Math.min(
  visibleConnections.value.length,
  Math.ceil((connectionScrollTop.value + connectionViewportHeight.value) / connectionRowHeight) + connectionVirtualOverscan,
))
const virtualConnections = computed(() => visibleConnections.value.slice(virtualConnectionStart.value, virtualConnectionEnd.value))
const virtualConnectionTop = computed(() => virtualConnectionStart.value * connectionRowHeight)
const virtualConnectionBottom = computed(() => Math.max(0, (visibleConnections.value.length - virtualConnectionEnd.value) * connectionRowHeight))

function finiteConnectionMetric(value: number) {
  return Number.isFinite(value) ? Math.max(0, value) : 0
}

function onConnectionsScroll() {
  const panel = connectionsPanel.value
  if (!panel) return
  connectionScrollTop.value = panel.scrollTop
  connectionViewportHeight.value = panel.clientHeight
}

watch([connectionSearch, connectionSort], () => {
  connectionScrollTop.value = 0
  if (connectionsPanel.value) connectionsPanel.value.scrollTop = 0
})

let connectionPanelResizeObserver: ResizeObserver | undefined
watch(connectionsPanel, (panel) => {
  connectionPanelResizeObserver?.disconnect()
  connectionPanelResizeObserver = undefined
  if (!panel) return
  connectionViewportHeight.value = panel.clientHeight
  connectionPanelResizeObserver = new ResizeObserver(() => {
    connectionViewportHeight.value = panel.clientHeight
  })
  connectionPanelResizeObserver.observe(panel)
})

const connectionSorts: Array<{ id: ConnectionSort; label: string }> = [
  { id: 'uploadSpeed', label: '上传速度' },
  { id: 'downloadSpeed', label: '下载速度' },
  { id: 'upload', label: '上传流量' },
  { id: 'download', label: '下载流量' },
  { id: 'time', label: '时间' },
]

const navItems: Array<{ id: PageId; label: string; icon: string }> = [
  { id: 'general', label: '常规', icon: 'nav-general' },
  { id: 'proxies', label: '代理', icon: 'nav-proxies' },
  { id: 'profiles', label: '配置', icon: 'nav-profiles' },
  { id: 'cores', label: '核心', icon: 'core' },
  { id: 'logs', label: '日志', icon: 'nav-logs' },
  { id: 'connections', label: '连接', icon: 'nav-connections' },
  { id: 'settings', label: '设置', icon: 'nav-settings' },
  { id: 'feedback', label: '反馈', icon: 'nav-feedback' },
]

const pageTitle = computed(() => navItems.find((item) => item.id === activePage.value)?.label ?? '常规')

function selectPage(page: PageId) {
  activePage.value = page
  if (page === 'proxies' && backendStatus.value !== 'loading') void loadProxyData()
  if (page === 'profiles') void Promise.all([loadProfiles(), loadCores(), loadProxyData()])
  if (page === 'cores') void Promise.all([loadProfiles(), loadCores()])
  if (page === 'logs') void loadCoreLogs()
  if (page === 'connections') void loadConnections()
}

let toastTimer: number | undefined

function showToast(message: string) {
  toastMessage.value = message
  if (toastTimer) window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => {
    toastMessage.value = ''
  }, 2200)
}

function toggleSetting(label: string, setting: { value: boolean }) {
  setting.value = !setting.value
  showToast(`${label}已${setting.value ? '开启' : '关闭'}（仅更新界面状态）`)
}

function toggleLan() {
  toggleSetting('局域网连接', allowLanEnabled)
}

function toggleIpv6() {
  toggleSetting('IPv6', ipv6Enabled)
}

function toggleTun() {
  toggleSetting('TUN 模式', tunEnabled)
}

async function loadSystemProxy() {
  try {
    const status = await getSystemProxy()
    systemProxyCoreId.value = status.enabled ? status.coreId : null
  } catch (error) {
    showToast(readableError(error, '无法读取 Windows 系统代理'))
  }
}

async function toggleCoreSystemProxy(coreId: string) {
  if (systemProxyPendingCoreId.value) return
  const core = cores.value.find((item) => item.id === coreId)
  if (!core) {
    showToast('核心不存在，无法设置系统代理')
    return
  }
  const enable = systemProxyCoreId.value !== coreId
  systemProxyPendingCoreId.value = coreId
  try {
    const status = await setSystemProxy(coreId, enable)
    systemProxyCoreId.value = status.enabled ? status.coreId : null
    showToast(enable ? `系统代理已切换到 ${core.name}` : '系统代理已关闭')
  } catch (error) {
    showToast(readableError(error, '系统代理设置失败'))
  } finally {
    systemProxyPendingCoreId.value = null
  }
}

async function loadStartWithWindows() {
  const api = (window as Window & { pywebview?: { api?: { get_start_with_windows?: () => Promise<{ success: boolean; enabled: boolean }> } } }).pywebview?.api
  if (!api?.get_start_with_windows) return
  try {
    const result = await api.get_start_with_windows()
    startWithWindowsEnabled.value = result.enabled
  } catch (error) {
    showToast(readableError(error, '无法读取开机启动状态'))
  }
}

async function toggleStartWithWindows() {
  const next = !startWithWindowsEnabled.value
  const api = (window as Window & { pywebview?: { api?: { set_start_with_windows?: (value: boolean) => Promise<{ success: boolean; enabled: boolean }> } } }).pywebview?.api
  if (!api?.set_start_with_windows) {
    showToast('开机启动仅支持桌面版')
    return
  }
  try {
    const result = await api.set_start_with_windows(next)
    startWithWindowsEnabled.value = result.enabled
    showToast(next ? '开机启动已安装' : '开机启动已卸载')
  } catch (error) {
    showToast(readableError(error, '开机启动设置失败'))
  }
}

function toggleRememberPage() {
  rememberPageEnabled.value = !rememberPageEnabled.value
  localStorage.setItem('ohmyclash.rememberPage', String(rememberPageEnabled.value))
  if (rememberPageEnabled.value) localStorage.setItem('ohmyclash.activePage', activePage.value)
  else localStorage.removeItem('ohmyclash.activePage')
  showToast(`启动时打开上次页面已${rememberPageEnabled.value ? '开启' : '关闭'}`)
}

async function syncTrayResident(enabled: boolean) {
  const api = (window as Window & { pywebview?: { api?: { set_tray_resident?: (value: boolean) => Promise<{ success: boolean }> } } }).pywebview?.api
  if (!api?.set_tray_resident) return false
  const result = await api.set_tray_resident(enabled)
  if (!result.success) throw new Error('无法更新托盘常驻状态')
  return true
}

let trayInitializationInFlight = false
async function initializeTrayResident() {
  if (!trayOnCloseEnabled.value || trayInitializationInFlight) return
  const api = (window as Window & { pywebview?: { api?: { set_tray_resident?: (value: boolean) => Promise<{ success: boolean }> } } }).pywebview?.api
  if (!api?.set_tray_resident) return
  trayInitializationInFlight = true
  try {
    await syncTrayResident(true)
  } catch (error) {
    showToast(readableError(error, '无法初始化托盘'))
  } finally {
    trayInitializationInFlight = false
  }
}

async function toggleTrayOnClose() {
  const next = !trayOnCloseEnabled.value
  try {
    await syncTrayResident(next)
    trayOnCloseEnabled.value = next
    localStorage.setItem('ohmyclash.trayResident', String(next))
    showToast(next ? '状态栏常驻已开启' : '状态栏常驻已关闭')
  } catch (error) {
    showToast(readableError(error, '状态栏常驻设置失败'))
  }
}

function notConnected(label: string) {
  showToast(`${label}功能暂未接入`)
}

function readableError(error: unknown, fallback: string) {
  if (error instanceof TypeError && /fetch/i.test(error.message)) return 'OhMyClash 后端未启动，请通过 npm run desktop 启动'
  return error instanceof Error ? error.message : fallback
}

async function loadProfiles() {
  if (profileStatus.value === 'loading') return
  profileStatus.value = 'loading'
  profileError.value = ''
  try {
    const response = await listProfiles()
    profiles.value = response.profiles
    if (!profiles.value.some((profile) => profile.id === selectedProfile.value)) {
      selectedProfile.value = profiles.value[0]?.id ?? ''
    }
    profileStatus.value = 'idle'
  } catch (error) {
    profileStatus.value = 'error'
    profileError.value = readableError(error, '无法读取配置列表')
  }
}

async function loadCores() {
  try {
    const nextCores = (await listCores()).cores
    const previousCores = cores.value
    const nextDrafts = Object.fromEntries(nextCores.map((core) => [core.id, {
      name: core.name,
      controllerPort: String(core.controllerPort),
      mixedPort: String(core.mixedPort),
    }])) as Record<string, CoreDraft>
    for (const previousCore of previousCores) {
      const draft = coreDrafts.value[previousCore.id]
      if (!draft) continue
      const hasUnsentChange = draft.name.trim() !== previousCore.name || draft.controllerPort.trim() !== String(previousCore.controllerPort) || draft.mixedPort.trim() !== String(previousCore.mixedPort)
      if (hasUnsentChange && nextDrafts[previousCore.id]) nextDrafts[previousCore.id] = draft
    }
    cores.value = nextCores
    const selectedCore = nextCores.find((core) => core.id === activeInstanceId.value)
    if (selectedCore) proxyMode.value = selectedCore.mode
    coreDrafts.value = nextDrafts
    await loadSystemProxy()
  } catch (error) {
    showToast(readableError(error, '无法读取核心列表'))
  }
}

async function refreshManagedInstances() {
  const response = await listManagedInstances()
  applyManagedInstances(response.instances)
  return response.instances
}

function applyManagedInstances(instances: ManagedInstanceStatus[]) {
  managedInstances.value = instances
  availableInstances.value = instances.map((instance) => ({ id: instance.id, name: instance.name }))
  if (!availableInstances.value.some((instance) => instance.id === activeInstanceId.value)) {
    activeInstanceId.value = availableInstances.value[0]?.id ?? ''
  }
}

async function pollManagedInstances() {
  if (statusLoading) return
  statusLoading = true
  try {
    await refreshManagedInstances()
  } catch (error) {
    managedInstances.value = []
    backendError.value = readableError(error, '无法刷新核心状态')
  } finally {
    statusLoading = false
  }
}

async function submitCore() {
  if (coreCreationPending.value || !coreName.value.trim() || !coreProfileId.value) return
  coreCreationPending.value = true
  try {
    const result = await createCore(coreName.value.trim(), coreProfileId.value)
    cores.value = [...cores.value, result.core]
    showCoreDialog.value = false
    coreName.value = ''
    activeInstanceId.value = result.core.id
    await loadProxyData()
    showToast(`${result.core.name} 已创建并启动`)
  } catch (error) {
    showToast(readableError(error, '核心创建或启动失败'))
  } finally {
    coreCreationPending.value = false
  }
}

async function patchCore(core: CoreSummary, changes: Record<string, unknown>) {
  if (coreMutationPending.value) return
  coreMutationPending.value = true
  try {
    await updateCore(core.id, changes)
    await loadCores()
    await loadProxyData()
  } catch (error) {
    showToast(readableError(error, '核心设置保存失败'))
  } finally {
    coreMutationPending.value = false
  }
}

function coreDraft(core: CoreSummary): CoreDraft {
  return coreDrafts.value[core.id] ?? {
    name: core.name,
    controllerPort: String(core.controllerPort),
    mixedPort: String(core.mixedPort),
  }
}

function updateCoreDraft(core: CoreSummary, key: keyof CoreDraft, event: Event) {
  const input = event.target as HTMLInputElement
  const current = coreDraft(core)
  coreDrafts.value = {
    ...coreDrafts.value,
    [core.id]: { ...current, [key]: input.value },
  }
}

function resetCoreDraft(core: CoreSummary) {
  coreDrafts.value = {
    ...coreDrafts.value,
    [core.id]: {
      name: core.name,
      controllerPort: String(core.controllerPort),
      mixedPort: String(core.mixedPort),
    },
  }
}

function coreDraftDirty(core: CoreSummary) {
  const draft = coreDraft(core)
  return draft.name.trim() !== core.name || draft.controllerPort.trim() !== String(core.controllerPort) || draft.mixedPort.trim() !== String(core.mixedPort)
}

function parseCorePort(value: string, label: string): number {
  const normalized = value.trim()
  if (!/^\d+$/.test(normalized)) throw new Error(`${label}必须是完整的数字`)
  const port = Number(normalized)
  if (!Number.isInteger(port) || port < 1024 || port > 65535) {
    throw new Error(`${label}必须在 1024 到 65535 之间`)
  }
  return port
}

async function saveCoreDraft(core: CoreSummary) {
  if (coreMutationPending.value || !coreDraftDirty(core)) return
  const draft = coreDraft(core)
  const name = draft.name.trim()
  let controllerPort: number
  let mixedPort: number
  try {
    if (!name) throw new Error('核心名称不能为空')
    controllerPort = parseCorePort(draft.controllerPort, '控制端口')
    mixedPort = parseCorePort(draft.mixedPort, '混合端口')
    if (controllerPort === mixedPort) throw new Error('控制端口和混合端口不能相同')
    const usedByOtherCore = cores.value.some((other) => other.id !== core.id && (
      [other.controllerPort, other.mixedPort].includes(controllerPort) ||
      [other.controllerPort, other.mixedPort].includes(mixedPort)
    ))
    if (usedByOtherCore) throw new Error('端口已被其他核心使用')
  } catch (error) {
    showToast(readableError(error, '核心设置无效'))
    return
  }

  coreMutationPending.value = true
  try {
    const response = await updateCore(core.id, { name, controllerPort, mixedPort })
    cores.value = cores.value.map((item) => item.id === response.core.id ? response.core : item)
    coreDrafts.value = {
      ...coreDrafts.value,
      [core.id]: { name, controllerPort: String(controllerPort), mixedPort: String(mixedPort) },
    }
    applyManagedInstances(response.instances)
    await loadProxyData(false)
    showToast(`${name} 的名称和端口已保存`)
  } catch (error) {
    showToast(readableError(error, '核心修改保存失败，已保持原配置'))
  } finally {
    coreMutationPending.value = false
  }
}

async function loadProxyData(showToastMessages = true): Promise<boolean> {
  const requestedInstanceId = activeInstanceId.value
  if (backendStatus.value === 'loading' && proxyLoadingInstanceId === requestedInstanceId) return false
  const requestId = ++proxyLoadRequestId
  proxyLoadingInstanceId = requestedInstanceId
  resetProxyTesting()
  backendStatus.value = 'loading'
  backendError.value = ''
  try {
    await refreshManagedInstances()
    if (availableInstances.value.length === 0) {
      throw new Error('当前没有已导入的代理配置')
    }
    if (requestId !== proxyLoadRequestId) return false
    const targetInstanceId = activeInstanceId.value
    const client = getMihomoClient(targetInstanceId)
    const [proxyResponse, ruleResponse] = await Promise.all([client.getProxies(), client.getRules()])
    if (requestId !== proxyLoadRequestId || activeInstanceId.value !== targetInstanceId) return false
    applyMihomoSnapshot(proxyResponse, ruleResponse)
    proxyDataLoaded.value = true
    backendStatus.value = 'connected'
    if (showToastMessages) showToast(`${activeInstance.value.name} 核心已连接`)
    return true
  } catch (error) {
    if (requestId !== proxyLoadRequestId) return false
    backendStatus.value = 'error'
    backendError.value = readableError(error, '无法连接 Mihomo controller')
    if (showToastMessages) showToast(`${activeInstance.value.name} 核心连接失败，请检查 Mihomo 进程和运行日志`)
    return false
  } finally {
    if (requestId === proxyLoadRequestId) proxyLoadingInstanceId = ''
  }
}

async function selectInstance(instanceId: MihomoInstanceId) {
  if (activeInstanceId.value === instanceId && backendStatus.value === 'loading') return
  showInstanceMenu.value = false
  activeInstanceId.value = instanceId
  const selectedCore = cores.value.find((core) => core.id === instanceId)
  if (selectedCore) proxyMode.value = selectedCore.mode
  connectionLoadGeneration += 1
  connectionLoadRequestId += 1
  connectionsLoading = false
  resetProxyTesting()
  connectionSamples.clear()
  connections.value = []
  uploadRate.value = 0
  downloadRate.value = 0
  await loadSystemProxy()
  proxyDataLoaded.value = false
  await loadProxyData()
  if (activePage.value === 'logs') await loadCoreLogs()
  if (activePage.value === 'connections') await loadConnections()
}

async function selectProxyMode(mode: ProxyMode) {
  if (proxyModePending.value || mode === proxyMode.value || !activeInstanceId.value) return
  const previousMode = proxyMode.value
  proxyModePending.value = true
  try {
    const response = await updateCoreMode(activeInstanceId.value, mode)
    cores.value = cores.value.map((core) => core.id === response.core.id ? response.core : core)
    proxyMode.value = response.core.mode
    showToast(`${activeInstance.value.name} 已切换到 ${mode} 模式`)
  } catch (error) {
    proxyMode.value = previousMode
    showToast(readableError(error, '核心模式切换失败'))
  } finally {
    proxyModePending.value = false
  }
}

async function selectProxy(name: string, groupName: string) {
  if (backendStatus.value !== 'connected') {
    showToast(`${activeInstance.value.name} 尚未连接，无法切换节点`)
    return
  }
  try {
    await getMihomoClient(activeInstanceId.value).selectProxy(groupName, name)
  } catch (error) {
    showToast(error instanceof Error ? error.message : '节点切换失败')
    return
  }
  if (groupName === 'GLOBAL') {
    selectedProxy.value = name
  } else {
    const group = ruleProxyGroups.value.find((item) => item.name === groupName)
    if (group) group.current = name
  }
  showToast(`已切换到 ${name}`)
}

async function testProxyGroup(groupName: string) {
  if (backendStatus.value !== 'connected') {
    showToast(`${activeInstance.value.name} 尚未连接，无法测速`)
    return
  }
  if (testingProxyGroups.value[groupName]) return

  const group = visibleProxyGroups.value.find((item) => item.name === groupName)
  const names = [...new Set((group?.items ?? []).map((item) => item.name))]
  const pendingNames = names.filter((name) => !checkingProxies.value[name])
  if (!pendingNames.length) return

  testingProxyGroups.value = { ...testingProxyGroups.value, [groupName]: true }
  checkingProxies.value = pendingNames.reduce<Record<string, true>>(
    (result, name) => ({ ...result, [name]: true }),
    { ...checkingProxies.value },
  )
  for (const name of pendingNames) updateProxyTestResult(name, { delay: undefined, testError: undefined })

  const client = getMihomoClient(activeInstanceId.value)
  if (!proxyTestAbortController || proxyTestAbortController.signal.aborted) proxyTestAbortController = new AbortController()
  const signal = proxyTestAbortController.signal
  const generation = proxyTestGeneration
  let nextIndex = 0
  const testNext = async () => {
    while (nextIndex < pendingNames.length) {
      const name = pendingNames[nextIndex]
      nextIndex += 1
      try {
        const delay = await client.checkProxy(name, undefined, proxyTestTimeout, signal)
        if (generation === proxyTestGeneration) updateProxyDelayResult(name, delay)
      } catch (error) {
        if (generation === proxyTestGeneration) {
          const message = error instanceof Error ? error.message : ''
          updateProxyTestResult(name, {
            delay: undefined,
            testError: /timeout|timed out|503|abort/i.test(message) ? 'Timeout' : 'Error',
          })
        }
      } finally {
        if (generation === proxyTestGeneration) {
          const next = { ...checkingProxies.value }
          delete next[name]
          checkingProxies.value = next
        }
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(proxyTestConcurrency, pendingNames.length) }, () => testNext()))

  if (generation === proxyTestGeneration) {
    const nextGroups = { ...testingProxyGroups.value }
    delete nextGroups[groupName]
    testingProxyGroups.value = nextGroups
  }
}

function resetProxyTesting() {
  proxyTestGeneration += 1
  proxyTestAbortController?.abort()
  proxyTestAbortController = null
  checkingProxies.value = {}
  testingProxyGroups.value = {}
}

function toggleProxySort() {
  proxySortMode.value = proxySortMode.value === 'delay' ? 'natural' : 'delay'
}

function toggleProxyGroup(groupName: string) {
  collapsedProxyGroups.value = {
    ...collapsedProxyGroups.value,
    [groupName]: !collapsedProxyGroups.value[groupName],
  }
}

async function checkProxy(name: string) {
  if (checkingProxies.value[name]) return
  if (backendStatus.value !== 'connected') {
    showToast(`${activeInstance.value.name} 尚未连接，无法检查节点`)
    return
  }
  checkingProxies.value = { ...checkingProxies.value, [name]: true }
  updateProxyTestResult(name, { delay: undefined, testError: undefined })
  if (!proxyTestAbortController || proxyTestAbortController.signal.aborted) proxyTestAbortController = new AbortController()
  const generation = proxyTestGeneration
  try {
    const delay = await getMihomoClient(activeInstanceId.value).checkProxy(name, undefined, 5000, proxyTestAbortController.signal)
    if (generation === proxyTestGeneration) updateProxyDelayResult(name, delay)
  } catch (error) {
    if (generation === proxyTestGeneration) {
      const message = error instanceof Error ? error.message : ''
      updateProxyTestResult(name, {
        delay: undefined,
        testError: /timeout|timed out|503|abort/i.test(message) ? 'Timeout' : 'Error',
      })
    }
  } finally {
    if (generation === proxyTestGeneration) {
      const next = { ...checkingProxies.value }
      delete next[name]
      checkingProxies.value = next
    }
  }
}

function updateProxyDelayResult(name: string, delay: number) {
  const valid = typeof delay === 'number' && Number.isFinite(delay) && delay > 0
  updateProxyTestResult(name, {
    delay: valid ? delay : undefined,
    testError: valid ? undefined : 'Timeout',
  })
}

function updateProxyTestResult(name: string, patch: Pick<ProxyNode, 'delay' | 'testError'>) {
  proxyNodes.value = proxyNodes.value.map((item) => item.name === name ? { ...item, ...patch } : item)
  ruleProxyGroups.value = ruleProxyGroups.value.map((group) => ({
    ...group,
    items: group.items.map((item) => item.name === name ? { ...item, ...patch } : item),
  }))
}

function proxyTestLabel(proxy: ProxyNode) {
  if (checkingProxies.value[proxy.name]) return 'Checking…'
  if (proxy.testError) return proxy.testError
  if (proxy.delay !== undefined) return `${proxy.delay} ms`
  return 'Check'
}

async function applyProfileToActiveCore(profileId: string): Promise<{ coreName: string; changed: boolean }> {
  const profile = profiles.value.find((item) => item.id === profileId)
  const core = activeCore.value
  if (!profile) throw new Error('配置不存在')
  if (!core) {
    throw new Error('请先在顶部选择核心')
  }
  if (coreMutationPending.value) {
    throw new Error('核心正在切换，请稍候')
  }
  if (core.profileId === profileId) {
    selectedProfile.value = profileId
    return { coreName: core.name, changed: false }
  }

  coreMutationPending.value = true
  switchingProfileId.value = profileId
  try {
    const response = await updateCore(core.id, { profileId })
    cores.value = cores.value.map((item) => item.id === response.core.id ? response.core : item)
    applyManagedInstances(response.instances)
    selectedProfile.value = profileId
    proxyDataLoaded.value = false
    if (!await loadProxyData(false)) {
      throw new Error(backendError.value || '切换后无法读取 Mihomo 核心状态')
    }
    return { coreName: core.name, changed: true }
  } finally {
    switchingProfileId.value = ''
    coreMutationPending.value = false
  }
}

async function selectProfile(profileId: string) {
  if (coreMutationPending.value) return
  const profile = profiles.value.find((item) => item.id === profileId)
  if (!profile) return
  try {
    const result = await applyProfileToActiveCore(profileId)
    showToast(result.changed ? `${profile.name} 已启用` : `${profile.name} 已在当前核心启用`)
  } catch (error) {
    showToast(`切换 ${profile.name} 失败：${readableError(error, '核心重载失败')}`)
  }
}

async function updateProfile(profileId: string) {
  if (updatingProfile.value) return
  const profile = profiles.value.find((item) => item.id === profileId)
  if (!profile || profile.sourceType !== 'subscription') {
    showToast('本地配置没有订阅地址')
    return
  }
  updatingProfile.value = profileId
  profileError.value = ''
  showToast(`正在更新 ${profile.name}`)
  try {
    await refreshProfile(profileId)
    await loadProfiles()
    await loadProxyData()
    showToast(`${profile.name} 更新完成`)
  } catch (error) {
    profileError.value = readableError(error, '订阅更新失败')
    showToast(`${profile.name} 更新失败`)
  } finally {
    updatingProfile.value = ''
  }
}

async function openProfileEditor(profileId: string) {
  try {
    const result = await getProfileContent(profileId)
    editingProfileId.value = profileId
    editingProfileName.value = result.profile.name
    editingProfileContent.value = result.content
  } catch (error) {
    showToast(readableError(error, '无法读取配置内容'))
  }
}

async function submitProfileEdit() {
  const result = await saveProfile(editingProfileId.value, editingProfileName.value, editingProfileContent.value)
  profiles.value = profiles.value.map((profile) => profile.id === result.profile.id ? result.profile : profile)
  editingProfileId.value = ''
  await loadProxyData()
}

function startProfileImport() {
  profileFileInput.value?.click()
}

async function handleProfileFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return

  profileStatus.value = 'loading'
  profileError.value = ''
  try {
    const content = await file.text()
    const name = file.name.replace(/\.(ya?ml)$/i, '') || '本地配置'
    const response = await importProfile(name, content)
    profiles.value = [...profiles.value.filter((profile) => profile.id !== response.profile.id), response.profile]
    selectedProfile.value = response.profile.id
    profileStatus.value = 'idle'
    showToast(`${response.profile.name} 已导入配置库`)
  } catch (error) {
    profileStatus.value = 'error'
    profileError.value = readableError(error, '配置导入失败')
    showToast('配置导入失败')
  }
}

function openSubscriptionDialog() {
  subscriptionName.value = ''
  subscriptionUrl.value = ''
  profileError.value = ''
  showSubscriptionDialog.value = true
}

function closeSubscriptionDialog() {
  if (profileStatus.value === 'loading') return
  showSubscriptionDialog.value = false
}

async function submitSubscription() {
  if (!subscriptionName.value.trim() || !subscriptionUrl.value.trim()) return
  profileStatus.value = 'loading'
  profileError.value = ''
  try {
    const response = await addSubscription(subscriptionName.value.trim(), subscriptionUrl.value.trim())
    profiles.value = [...profiles.value.filter((profile) => profile.id !== response.profile.id), response.profile]
    selectedProfile.value = response.profile.id
    showSubscriptionDialog.value = false
    profileStatus.value = 'idle'
    try {
      const result = await applyProfileToActiveCore(response.profile.id)
      showToast(result.changed ? `${response.profile.name} 已添加并启用` : `${response.profile.name} 已在当前核心启用`)
    } catch (error) {
      showToast(`${response.profile.name} 已添加，但启用失败：${readableError(error, '核心重载失败')}`)
    }
  } catch (error) {
    profileStatus.value = 'error'
    profileError.value = readableError(error, '订阅添加失败')
    showToast('订阅添加失败')
  }
}

function profileUpdatedAt(value?: string | null) {
  if (!value) return '尚未记录更新时间'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '已保存'
  return date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function openContextMenu(event: MouseEvent, kind: 'profile' | 'core', id: string) {
  const item = kind === 'profile' ? profiles.value.find((profile) => profile.id === id) : cores.value.find((core) => core.id === id)
  if (!item) return
  contextMenu.value = {
    kind,
    id,
    x: Math.min(event.clientX, window.innerWidth - 155),
    y: Math.min(event.clientY, window.innerHeight - 70),
  }
}

function requestDeleteFromContextMenu() {
  const menu = contextMenu.value
  if (!menu) return
  const item = menu.kind === 'profile' ? profiles.value.find((profile) => profile.id === menu.id) : cores.value.find((core) => core.id === menu.id)
  contextMenu.value = null
  if (item) deleteTarget.value = { kind: menu.kind, id: menu.id, name: item.name }
}

async function confirmDelete() {
  const target = deleteTarget.value
  if (!target || deletingTarget.value) return
  deletingTarget.value = true
  try {
    if (target.kind === 'profile') {
      await deleteProfile(target.id)
      if (selectedProfile.value === target.id) selectedProfile.value = ''
      await loadProfiles()
    } else {
      await deleteCore(target.id)
      if (activeInstanceId.value === target.id) activeInstanceId.value = ''
      await Promise.all([loadCores(), loadProxyData()])
    }
    deleteTarget.value = null
    showToast(`${target.name} 已删除`)
  } catch (error) {
    showToast(readableError(error, '删除失败'))
  } finally {
    deletingTarget.value = false
  }
}

function parseCoreLog(line: string): LogEntry {
  const time = line.match(/\btime=["']?\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})/i)?.[1]
    ?? line.match(/(?:T|\s)(\d{2}:\d{2}:\d{2})(?:[.,+Z\s])/i)?.[1]
    ?? '--:--:--'
  const rawLevel = line.match(/(?:level=|\b)(INFO|WARN(?:ING)?|ERROR|FATAL|DEBUG)\b/i)?.[1]?.toUpperCase() ?? 'INFO'
  const level: LogLevel = rawLevel === 'ERROR' || rawLevel === 'FATAL' ? 'ERROR' : rawLevel.startsWith('WARN') ? 'WARN' : 'INFO'
  return { time, level, message: line }
}

function onLogScroll() {
  const panel = logPanel.value
  if (!panel) return
  logStickToBottom.value = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 24
}

async function ensureActiveInstance(): Promise<ManagedInstanceStatus[]> {
  const instances = await refreshManagedInstances()
  if (!availableInstances.value.length) throw new Error('当前没有可用核心')
  return instances
}

async function loadCoreLogs() {
  if (logsLoading) return
  logsLoading = true
  try {
    if (!activeInstanceId.value || !availableInstances.value.some((instance) => instance.id === activeInstanceId.value)) {
      await ensureActiveInstance()
    }
    const instanceId = activeInstanceId.value
    const lines = await getMihomoClient(instanceId).getRuntimeLogs()
    if (instanceId !== activeInstanceId.value) return
    logs.value = lines.map(parseCoreLog)
  } catch (error) {
    logs.value = []
    backendError.value = readableError(error, '无法读取核心日志')
  } finally {
    logsLoading = false
  }
}

function formatTraffic(bytes = 0) {
  if (bytes < 1024) return `${bytes < 10 ? bytes.toFixed(1) : Math.round(bytes)} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(2)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

function formatRate(bytes: number) {
  return `${formatTraffic(Math.max(0, bytes))}/s`
}

function connectionAge(startedAt: number) {
  if (!startedAt) return '刚刚'
  const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000))
  if (seconds < 60) return `${seconds} 秒前`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`
  return `${Math.floor(seconds / 86400)} 天前`
}

function mapConnection(connection: MihomoConnection, sampledAt: number): Connection {
  const metadata = connection.metadata ?? {}
  const destination = [metadata.destinationIP, metadata.destinationPort].filter(Boolean).join(':')
  const previous = connectionSamples.get(connection.id)
  const elapsed = previous ? Math.max((sampledAt - previous.sampledAt) / 1000, 0.001) : 0
  const upload = connection.upload ?? 0
  const download = connection.download ?? 0
  connectionSamples.set(connection.id, { upload, download, sampledAt })
  return {
    id: connection.id,
    host: metadata.host || metadata.sniffHost || destination || '未知目标',
    address: destination || '-',
    network: (metadata.network || '-').toUpperCase(),
    inbound: metadata.type || '-',
    process: metadata.process || metadata.processPath?.split(/[\\/]/).pop() || '-',
    policy: connection.chains?.[0] || [connection.rule, connection.rulePayload].filter(Boolean).join(' / ') || '-',
    startedAt: connection.start ? new Date(connection.start).getTime() : sampledAt,
    upload,
    download,
    uploadSpeed: elapsed ? finiteConnectionMetric((upload - previous!.upload) / elapsed) : 0,
    downloadSpeed: elapsed ? finiteConnectionMetric((download - previous!.download) / elapsed) : 0,
  }
}

async function loadConnections() {
  if (connectionsPaused.value || connectionsLoading) return
  const generation = connectionLoadGeneration
  const requestId = ++connectionLoadRequestId
  connectionsLoading = true
  try {
    if (!activeInstanceId.value || !availableInstances.value.some((instance) => instance.id === activeInstanceId.value)) {
      await ensureActiveInstance()
    }
    if (generation !== connectionLoadGeneration || requestId !== connectionLoadRequestId) return
    const instanceId = activeInstanceId.value
    const response = await getMihomoClient(instanceId).getConnections()
    if (generation !== connectionLoadGeneration || requestId !== connectionLoadRequestId || instanceId !== activeInstanceId.value) return
    const sampledAt = Date.now()
    const nextConnections: Connection[] = []
    const activeIds = new Set<string>()
    let nextUploadRate = 0
    let nextDownloadRate = 0
    let calculatedUploadTotal = 0
    let calculatedDownloadTotal = 0
    for (const rawConnection of response.connections ?? []) {
      const connection = mapConnection(rawConnection, sampledAt)
      nextConnections.push(connection)
      activeIds.add(connection.id)
      nextUploadRate += connection.uploadSpeed
      nextDownloadRate += connection.downloadSpeed
      calculatedUploadTotal += connection.upload
      calculatedDownloadTotal += connection.download
    }
    connections.value = nextConnections
    for (const id of connectionSamples.keys()) if (!activeIds.has(id)) connectionSamples.delete(id)
    uploadTotal.value = response.uploadTotal ?? calculatedUploadTotal
    downloadTotal.value = response.downloadTotal ?? calculatedDownloadTotal
    uploadRate.value = nextUploadRate
    downloadRate.value = nextDownloadRate
  } catch (error) {
    if (generation !== connectionLoadGeneration || requestId !== connectionLoadRequestId) return
    connections.value = []
    backendError.value = readableError(error, '无法读取核心连接')
  } finally {
    if (requestId === connectionLoadRequestId) connectionsLoading = false
  }
}

function toggleConnectionsPaused() {
  connectionsPaused.value = !connectionsPaused.value
  if (!connectionsPaused.value) void loadConnections()
}

async function clearLogs() {
  try {
    await ensureActiveInstance()
    await getMihomoClient(activeInstanceId.value).clearRuntimeLogs()
    logs.value = []
  } catch (error) {
    showToast(readableError(error, '核心日志清空失败'))
  }
}

async function closeConnection(id: string) {
  try {
    await getMihomoClient(activeInstanceId.value).closeConnection(id)
    connections.value = connections.value.filter((connection) => connection.id !== id)
  } catch (error) {
    showToast(readableError(error, '关闭连接失败'))
  }
}

async function closeAllConnections() {
  try {
    await getMihomoClient(activeInstanceId.value).closeAllConnections()
    connections.value = []
  } catch (error) {
    showToast(readableError(error, '关闭全部连接失败'))
  }
}

let liveDataTimer: number | undefined
let uptimeTimer: number | undefined
let statusTimer: number | undefined
const handlePywebviewReady = () => {
  void initializeTrayResident()
  void loadStartWithWindows()
}
onMounted(() => {
  void loadCores()
  void pollManagedInstances()
  if (activePage.value !== 'general') selectPage(activePage.value)
  window.addEventListener('pywebviewready', handlePywebviewReady)
  void initializeTrayResident()
  void loadStartWithWindows()
  window.addEventListener('pointerdown', closeContextMenu)
  document.addEventListener('visibilitychange', refreshVisiblePage)
  liveDataTimer = window.setInterval(() => {
    if (document.hidden) return
    if (activePage.value === 'logs') void loadCoreLogs()
    if (activePage.value === 'connections') void loadConnections()
  }, 2000)
  uptimeTimer = window.setInterval(() => { uptimeClock.value = Date.now() }, 1000)
  statusTimer = window.setInterval(() => { if (!document.hidden) void pollManagedInstances() }, 10000)
})
onBeforeUnmount(() => {
  if (liveDataTimer) window.clearInterval(liveDataTimer)
  if (uptimeTimer) window.clearInterval(uptimeTimer)
  if (statusTimer) window.clearInterval(statusTimer)
  window.removeEventListener('pywebviewready', handlePywebviewReady)
  window.removeEventListener('pointerdown', closeContextMenu)
  document.removeEventListener('visibilitychange', refreshVisiblePage)
  connectionPanelResizeObserver?.disconnect()
})

function refreshVisiblePage() {
  if (document.hidden) return
  void pollManagedInstances()
  if (activePage.value === 'logs') void loadCoreLogs()
  if (activePage.value === 'connections') void loadConnections()
}

function closeContextMenu() {
  contextMenu.value = null
}

function windowAction(label: string) {
  const actions: Record<string, string> = { '最小化': 'minimize_window', '最大化': 'maximize_window', '关闭': 'close_window' }
  const api = (window as Window & { pywebview?: { api?: { webview_window_action?: (action: string, prefix: string, payload: object) => Promise<unknown> } } }).pywebview?.api
  if (api?.webview_window_action && actions[label]) void api.webview_window_action(actions[label], '', {})
}
</script>

<template>
  <div class="cfw-window">
    <svg class="icon-sprite" aria-hidden="true">
      <symbol id="icon-pin" viewBox="0 0 24 24"><path d="M8 3h8l-1.3 5.2 3.2 3.2v1.1H13v6.2l-1 2-1-2v-6.2H6.1v-1.1l3.2-3.2L8 3Z" /></symbol>
      <symbol id="icon-minimize" viewBox="0 0 24 24"><path d="M5 12h14v1.8H5z" /></symbol>
      <symbol id="icon-maximize" viewBox="0 0 24 24"><path d="M5 5h14v14H5V5Zm1.8 1.8v10.4h10.4V6.8H6.8Z" /></symbol>
      <symbol id="icon-close" viewBox="0 0 24 24"><path d="m6.4 5.1 6 6 6-6 1.3 1.3-6 6 6 6-1.3 1.3-6-6-6 6-1.3-1.3 6-6-6-6 1.3-1.3Z" /></symbol>
      <symbol id="icon-connection-close" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9.25" /><path d="m8.5 8.5 7 7m0-7-7 7" /></symbol>
      <symbol id="icon-nav-general" viewBox="0 0 24 24"><path d="M12 19V5m0 0L6.8 10.2M12 5l5.2 5.2" /></symbol>
      <symbol id="icon-nav-proxies" viewBox="0 0 24 24"><circle cx="6" cy="12" r="2.2" /><circle cx="18" cy="6" r="2.2" /><circle cx="18" cy="18" r="2.2" /><path d="m8 11 7.8-4M8 13l7.8 4" /></symbol>
      <symbol id="icon-nav-profiles" viewBox="0 0 24 24"><path d="M6 3.8h8l4 4V20H6V3.8Zm8 0v4h4M8.8 12h6.4M8.8 15.5h6.4" /></symbol>
      <symbol id="icon-nav-logs" viewBox="0 0 24 24"><path d="M5 6h14M5 12h14M5 18h14" /></symbol>
      <symbol id="icon-nav-connections" viewBox="0 0 24 24"><path d="M8.5 8.5 15.5 15.5M6.5 14.5l-2 2a3.2 3.2 0 0 0 4.5 4.5l2-2M17.5 9.5l2-2A3.2 3.2 0 0 0 15 3l-2 2" /></symbol>
      <symbol id="icon-nav-settings" viewBox="0 0 24 24"><path d="m12 3 1.3 2.2 2.5.7 2.2-1.1 1.2 1.2-1.1 2.2.7 2.5L21 12l-2.2 1.3-.7 2.5 1.1 2.2-1.2 1.2-2.2-1.1-2.5.7L12 21l-1.3-2.2-2.5-.7-2.2 1.1-1.2-1.2 1.1-2.2-.7-2.5L3 12l2.2-1.3.7-2.5-1.1-2.2L6 4.8l2.2 1.1 2.5-.7L12 3Z" /><circle cx="12" cy="12" r="3" /></symbol>
      <symbol id="icon-nav-feedback" viewBox="0 0 24 24"><path d="M5 5.5h14v10H11l-4 3v-3H5v-10Z" /><path d="M8.5 9.5h7M8.5 12.5h4" /></symbol>
      <symbol id="icon-terminal" viewBox="0 0 24 24"><path d="M4 5h16v14H4V5Zm3 4 3 3-3 3M12.5 15H17" /></symbol>
      <symbol id="icon-muted" viewBox="0 0 24 24"><path d="m5 5 14 14M9 9l3-3 3 3v5l-2 2M5 12h3" /></symbol>
      <symbol id="icon-lan" viewBox="0 0 24 24"><circle cx="12" cy="5" r="2" /><circle cx="6" cy="18" r="2" /><circle cx="18" cy="18" r="2" /><path d="M12 7v5M12 12H6v4M12 12h6v4" /></symbol>
      <symbol id="icon-alert" viewBox="0 0 24 24"><path d="M12 3 21 20H3L12 3Z" /><path d="M12 9v5M12 17h.01" /></symbol>
      <symbol id="icon-core" viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="1" /><path d="M9 3v4M12 3v4M15 3v4M9 17v4M12 17v4M15 17v4M3 9h4M3 12h4M3 15h4M17 9h4M17 12h4M17 15h4" /></symbol>
      <symbol id="icon-info" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" /><path d="M12 10.5v5M12 7.5h.01" /></symbol>
      <symbol id="icon-gear" viewBox="0 0 24 24"><path d="m12 3 1.2 2.2 2.4.7 2.2-1.1 1.4 1.4-1.1 2.2.7 2.4L21 12l-2.2 1.2-.7 2.4 1.1 2.2-1.4 1.4-2.2-1.1-2.4.7L12 21l-1.2-2.2-2.4-.7-2.2 1.1-1.4-1.4 1.1-2.2-.7-2.4L3 12l2.2-1.2.7-2.4-1.1-2.2 1.4-1.4 2.2 1.1 2.4-.7L12 3Z" /><circle cx="12" cy="12" r="3" /></symbol>
      <symbol id="icon-test-all" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4M8.5 11h5M11 8.5v5M18 5v4M16 7h4" /></symbol>
      <symbol id="icon-signal" viewBox="0 0 24 24"><path d="M4 9.5a11 11 0 0 1 16 0M7 13a7 7 0 0 1 10 0M10 16.5a3 3 0 0 1 4 0M12 20h.01" /></symbol>
      <symbol id="icon-eye-off" viewBox="0 0 24 24"><path d="M3.5 3.5 20.5 20.5M10.6 10.6a2 2 0 0 0 2.8 2.8M6.7 6.7C4.8 8 3.5 10 3 12c1.4 3.6 4.6 6 9 6 1.5 0 2.8-.3 4-.9M9.5 5.1C10.3 4.8 11.1 4.6 12 4.6c4.4 0 7.6 2.4 9 7.4-.5 1.4-1.2 2.6-2.2 3.6" /></symbol>
      <symbol id="icon-refresh" viewBox="0 0 24 24"><path d="M20 11a8 8 0 0 0-14-4L4 9M4 5v4h4M4 13a8 8 0 0 0 14 4l2-2M20 19v-4h-4" /></symbol>
      <symbol id="icon-collapse" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6" /></symbol>
      <symbol id="icon-upload" viewBox="0 0 24 24"><path d="M12 20V5M6.5 10.5 12 5l5.5 5.5" /></symbol>
      <symbol id="icon-download" viewBox="0 0 24 24"><path d="M12 4v15m-5.5-5.5L12 19l5.5-5.5" /></symbol>
    </svg>
    <header class="titlebar">
      <div class="titlebar-drag-area" />
      <div class="window-actions" aria-label="窗口控制">
        <button class="window-action pin-action" title="置顶" @click="windowAction('置顶')"><svg><use href="#icon-pin" /></svg></button>
        <button class="window-action" title="最小化" @click="windowAction('最小化')"><svg><use href="#icon-minimize" /></svg></button>
        <button class="window-action maximize-action" title="最大化" @click="windowAction('最大化')"><svg><use href="#icon-maximize" /></svg></button>
        <button class="window-action close-action" title="关闭" @click="windowAction('关闭')"><svg><use href="#icon-close" /></svg></button>
      </div>
    </header>

    <div class="window-body">
      <aside class="sidebar">
        <section class="traffic-panel" aria-label="实时流量">
          <div class="traffic-line"><svg class="traffic-arrow" aria-hidden="true"><use href="#icon-upload" /></svg><strong>{{ formatRate(uploadRate) }}</strong></div>
          <div class="traffic-line"><svg class="traffic-arrow" aria-hidden="true"><use href="#icon-download" /></svg><strong>{{ formatRate(downloadRate) }}</strong></div>
        </section>

        <nav class="main-nav" aria-label="主导航">
          <button
            v-for="item in navItems"
            :key="item.id"
            class="nav-item"
            :class="{ selected: activePage === item.id }"
            @click="selectPage(item.id)"
          >
            <svg class="nav-icon"><use :href="`#icon-${item.icon}`" /></svg>
            <span>{{ item.label }}</span>
          </button>
        </nav>

        <section class="connection-status">
          <div class="uptime">{{ coreUptime }}</div>
          <div class="connected"><span class="status-dot" :class="{ green: backendStatus === 'connected', red: backendStatus === 'error', idle: backendStatus !== 'connected' && backendStatus !== 'error' }" />{{ connectionStatusLabel }}</div>
        </section>
      </aside>

      <main class="content-area">
        <section v-if="activePage === 'general'" class="brand-header">
          <svg class="cat-logo" viewBox="0 0 110 110" aria-label="OhMyClash logo">
            <path class="cat-body" d="M23 77c4-17 4-42 9-60l16 18c8-3 17-3 25 0l16-18c5 18 5 43 9 60-11 9-24 13-37 13S34 86 23 77Z" />
            <circle class="cat-eye" cx="47" cy="51" r="4.8" />
            <circle class="cat-eye" cx="75" cy="51" r="4.8" />
            <path class="cat-mouth" d="M56 62c3 3 7 3 10 0M61 62v5" />
          </svg>
          <div class="brand-title">OhMyClash <span>v0.1.0</span></div>
        </section>

        <section v-if="activePage === 'general'" class="settings-list" aria-label="常规设置">
          <div class="setting-row">
            <div class="setting-name">开机启动</div>
            <button class="setting-value setting-control" @click="toggleStartWithWindows"><span class="switch" :class="{ on: startWithWindowsEnabled }"><i /></span></button>
          </div>
        </section>

        <section v-else-if="activePage === 'proxies'" class="screen-page proxy-page">
          <div class="proxy-instance-bar">
            <div class="instance-select">
              <button class="instance-select-trigger" :aria-expanded="showInstanceMenu" @click="showInstanceMenu = !showInstanceMenu">
                <span>{{ activeInstance.name }}</span><svg :class="{ open: showInstanceMenu }"><use href="#icon-collapse" /></svg>
              </button>
              <div v-if="showInstanceMenu" class="instance-select-menu">
                <button v-for="instance in availableInstances" :key="instance.id" :class="{ active: activeInstanceId === instance.id }" @click="selectInstance(instance.id)">{{ instance.name }}</button>
              </div>
            </div>
            <div class="proxy-instance-status" :class="`status-${backendStatus}`" :title="backendError"><i />{{ backendStatusLabel }}</div>
            <button class="proxy-instance-refresh" title="刷新核心数据" :disabled="backendStatus === 'loading'" @click="() => loadProxyData()"><svg><use href="#icon-refresh" /></svg></button>
          </div>
          <div class="proxy-mode-tabs" role="tablist" aria-label="代理模式">
            <button v-for="mode in proxyModes" :key="mode.id" class="proxy-mode-tab" :class="{ active: proxyMode === mode.id }" role="tab" :aria-selected="proxyMode === mode.id" :disabled="proxyModePending" @click="selectProxyMode(mode.id)">{{ mode.label }}</button>
          </div>

          <div v-if="visibleProxyGroups.length === 0" class="proxy-data-empty"><strong>{{ backendStatusLabel }}</strong><p>{{ backendError || '先在配置页导入本地 YAML 或添加订阅，核心启动后这里会显示真实代理组。' }}</p><button class="page-button secondary" @click="selectPage('profiles')">前往配置</button></div>
          <div v-for="(group, groupIndex) in visibleProxyGroups" :key="group.name" class="proxy-group-section" :class="{ 'first-group': groupIndex === 0 }">
            <div
              class="proxy-toolbar"
              role="button"
              tabindex="0"
              :aria-expanded="!collapsedProxyGroups[group.name]"
              @click="toggleProxyGroup(group.name)"
              @keydown.enter.prevent="toggleProxyGroup(group.name)"
              @keydown.space.prevent="toggleProxyGroup(group.name)"
            >
              <button class="proxy-group-title" :aria-expanded="!collapsedProxyGroups[group.name]" @click.stop="toggleProxyGroup(group.name)">
                <span>{{ group.name }}</span><span class="proxy-type-mark">S</span><span class="proxy-current">{{ group.current }}</span>
              </button>
              <div class="proxy-tools">
                <button class="proxy-tool" title="测速所有子项" :disabled="!!testingProxyGroups[group.name]" @click.stop="testProxyGroup(group.name)"><svg><use href="#icon-test-all" /></svg></button>
                <button class="proxy-tool proxy-sort-tool" :class="{ active: proxySortMode === 'delay' }" :aria-pressed="proxySortMode === 'delay'" :title="proxySortMode === 'delay' ? '恢复自然顺序' : '按延迟从低到高排序'" @click.stop="toggleProxySort"><span>{{ proxySortMode === 'delay' ? '自然' : '延迟' }}</span></button>
                <button class="proxy-tool collapse-tool" :class="{ collapsed: collapsedProxyGroups[group.name] }" :title="collapsedProxyGroups[group.name] ? '展开分组' : '折叠分组'" @click.stop="toggleProxyGroup(group.name)"><svg><use href="#icon-collapse" /></svg></button>
              </div>
            </div>

            <div v-if="!collapsedProxyGroups[group.name]" class="proxy-card-grid">
              <article v-for="proxy in group.items" :key="`${group.name}-${proxy.name}`" class="proxy-card" :class="{ active: group.current === proxy.name }" @click="selectProxy(proxy.name, group.name)">
                <span class="proxy-status-strip" :class="{ active: group.current === proxy.name }" />
                <div class="proxy-card-body">
                  <strong>{{ proxy.name }}</strong>
                  <div class="proxy-card-meta"><span>{{ proxy.type }}</span><span v-if="proxy.badge" class="protocol-badge">{{ proxy.badge }}</span></div>
                </div>
                <button
                  class="proxy-check"
                  :class="{ 'has-delay': proxy.delay !== undefined && !proxy.testError && !checkingProxies[proxy.name], 'has-error': !!proxy.testError && !checkingProxies[proxy.name] }"
                  :disabled="!!checkingProxies[proxy.name]"
                  @click.stop="checkProxy(proxy.name)"
                >{{ proxyTestLabel(proxy) }}</button>
              </article>
            </div>
          </div>
        </section>

        <section v-else-if="activePage === 'profiles'" class="screen-page">
          <div class="profile-core-bar"><span>应用到核心</span><div class="instance-select"><button class="instance-select-trigger" @click="showInstanceMenu = !showInstanceMenu"><span>{{ activeInstance.name }}</span><svg :class="{ open: showInstanceMenu }"><use href="#icon-collapse" /></svg></button><div v-if="showInstanceMenu" class="instance-select-menu"><button v-for="instance in availableInstances" :key="instance.id" :class="{ active: activeInstanceId === instance.id }" @click="selectInstance(instance.id)">{{ instance.name }}</button></div></div></div>
          <div class="screen-heading"><div><h2>配置</h2><p>点击配置即切换顶部所选核心的代理文件</p></div><div class="heading-actions"><button class="page-button secondary" :disabled="profileStatus === 'loading'" @click="startProfileImport">导入配置</button><button class="page-button" :disabled="profileStatus === 'loading'" @click="openSubscriptionDialog">添加订阅</button></div></div>
          <div v-if="profileError && profiles.length" class="profile-error">{{ profileError }}</div>
          <div v-if="profileStatus === 'loading'" class="profile-empty profile-loading"><strong>正在读取配置</strong><p>正在加载本地配置列表，请稍候。</p></div>
          <div v-else-if="profiles.length" class="profile-list">
            <article v-for="profile in profiles" :key="profile.id" class="profile-card" :class="{ active: activeProfileId === profile.id, switching: switchingProfileId === profile.id }" :aria-busy="switchingProfileId === profile.id" @click="selectProfile(profile.id)" @contextmenu.prevent="openContextMenu($event, 'profile', profile.id)">
              <div class="profile-leading"><span class="profile-mark" :class="{ violet: profile.sourceType === 'subscription' }">{{ profile.sourceType === 'subscription' ? 'S' : 'L' }}</span><div><strong>{{ profile.name }}</strong><small>{{ profile.sourceType === 'subscription' ? '远程订阅' : '本地 YAML' }} · {{ profileUpdatedAt(profile.updatedAt) }}</small></div></div>
              <div class="profile-meta"><button :disabled="coreMutationPending" @click.stop="openProfileEditor(profile.id)">编辑</button><button :disabled="coreMutationPending" @click.stop="revealProfile(profile.id)">文件夹</button><button v-if="profile.sourceType === 'subscription'" :disabled="updatingProfile === profile.id || coreMutationPending" @click.stop="updateProfile(profile.id)">{{ updatingProfile === profile.id ? '更新中…' : '更新' }}</button></div>
            </article>
          </div>
          <div v-else-if="profileStatus === 'error'" class="profile-empty profile-load-error"><strong>配置列表读取失败</strong><p>{{ profileError }}</p><div class="profile-empty-actions"><button class="page-button secondary" @click="loadProfiles">重试</button></div></div>
          <div v-else class="profile-empty"><span class="profile-empty-mark">+</span><strong>暂无代理配置</strong><p>先导入本地 YAML 或添加订阅，再到核心页选择配置并创建实例。</p><div class="profile-empty-actions"><button class="page-button secondary" @click="startProfileImport">导入 YAML</button><button class="page-button" @click="openSubscriptionDialog">添加订阅</button></div></div>
        </section>

        <section v-else-if="activePage === 'cores'" class="screen-page">
          <div class="screen-heading"><div><h2>核心</h2><p>多个 Mihomo 核心并行运行，系统代理仅绑定其中一个核心；名称和端口修改后统一保存</p></div><button class="page-button" @click="coreProfileId = profiles[0]?.id ?? ''; showCoreDialog = true">创建核心</button></div>
          <div class="core-list">
            <article v-for="core in cores" :key="core.id" class="core-card" :class="{ active: activeInstanceId === core.id }" @contextmenu.prevent="openContextMenu($event, 'core', core.id)">
              <div class="core-card-heading"><button @click="selectInstance(core.id)"><strong>{{ core.name }}</strong><small>{{ profiles.find((profile) => profile.id === core.profileId)?.name ?? core.profileId }}</small></button><span>{{ core.mixedPort }}</span></div>
              <label class="core-input-row"><span>名称</span><input :value="coreDraft(core).name" maxlength="80" autocomplete="off" @input="updateCoreDraft(core, 'name', $event)" @keydown.enter.prevent="saveCoreDraft(core)" @keydown.esc="resetCoreDraft(core)" /></label>
              <label class="core-input-row"><span>控制端口</span><input :value="coreDraft(core).controllerPort" type="text" inputmode="numeric" autocomplete="off" @input="updateCoreDraft(core, 'controllerPort', $event)" @keydown.enter.prevent="saveCoreDraft(core)" @keydown.esc="resetCoreDraft(core)" /></label>
              <label class="core-input-row"><span>混合端口</span><input :value="coreDraft(core).mixedPort" type="text" inputmode="numeric" autocomplete="off" @input="updateCoreDraft(core, 'mixedPort', $event)" @keydown.enter.prevent="saveCoreDraft(core)" @keydown.esc="resetCoreDraft(core)" /></label>
              <div class="core-port-actions"><span v-if="coreDraftDirty(core)">名称或端口修改尚未保存</span><button class="core-port-save" :disabled="coreMutationPending || !coreDraftDirty(core)" @click.stop="saveCoreDraft(core)">{{ coreMutationPending ? '保存中…' : '保存修改' }}</button></div>
              <div class="core-setting-row"><span>允许局域网</span><button @click="patchCore(core, { allowLan: !core.allowLan })"><span class="switch" :class="{ on: core.allowLan }"><i /></span></button></div>
              <div class="core-setting-row"><span>IPv6</span><button @click="patchCore(core, { ipv6: !core.ipv6 })"><span class="switch" :class="{ on: core.ipv6 }"><i /></span></button></div>
              <div class="core-setting-row"><span>TUN 模式</span><button @click="patchCore(core, { tunEnabled: !core.tunEnabled })"><span class="switch" :class="{ on: core.tunEnabled }"><i /></span></button></div>
              <div class="core-setting-row"><span>系统代理<small v-if="systemProxyCoreId === core.id">当前绑定</small><small v-else-if="systemProxyCoreId">其他核心已绑定</small></span><button :disabled="coreMutationPending || systemProxyPendingCoreId !== null" @click.stop="toggleCoreSystemProxy(core.id)"><span class="switch" :class="{ on: systemProxyCoreId === core.id }"><i /></span></button></div>
            </article>
          </div>
        </section>

        <section v-else-if="activePage === 'logs'" class="screen-page log-page">
          <div class="screen-heading"><div><h2>日志</h2><p>显示当前实例的核心运行日志</p></div><div class="heading-actions"><button class="page-button secondary" @click="showErrorsOnly = !showErrorsOnly">{{ showErrorsOnly ? '显示全部' : '仅错误' }}</button><button class="page-button secondary" @click="clearLogs">清空</button></div></div>
          <div ref="logPanel" class="log-panel" @scroll="onLogScroll">
            <div v-if="visibleLogs.length === 0" class="panel-empty">暂无日志</div>
            <div v-for="log in visibleLogs" :key="`${log.time}-${log.message}`" class="log-line"><time>{{ log.time }}</time><span class="log-level" :class="log.level.toLowerCase()">{{ log.level }}</span><span>{{ log.message }}</span></div>
          </div>
        </section>

        <section v-else-if="activePage === 'connections'" class="screen-page connections-page">
          <div class="connections-heading"><h2>连接 <span class="heading-count">{{ connections.length }}</span></h2><input v-model="connectionSearch" class="search-box" placeholder="搜索" /><button class="page-button pause-button" :class="{ active: connectionsPaused }" @click="toggleConnectionsPaused">{{ connectionsPaused ? '继续' : '暂停' }}</button><div class="connection-totals">总计: <span>↑{{ formatTraffic(uploadTotal) }}</span> <span>↓{{ formatTraffic(downloadTotal) }}</span></div></div>
          <div class="connection-toolbar"><div class="connection-sorts"><button v-for="sort in connectionSorts" :key="sort.id" :class="{ active: connectionSort === sort.id }" @click="connectionSort = sort.id">{{ sort.label }}</button></div><button class="page-button close-all-button" :disabled="connections.length === 0" @click="closeAllConnections">关闭全部</button></div>
          <div ref="connectionsPanel" class="connections-panel" @scroll.passive="onConnectionsScroll">
            <div v-if="visibleConnections.length === 0" class="panel-empty">暂无匹配连接</div>
            <div v-if="virtualConnectionTop" aria-hidden="true" :style="{ height: `${virtualConnectionTop}px` }" />
            <div v-for="connection in virtualConnections" :key="connection.id" class="connection-line"><div class="connection-detail"><strong>{{ connection.host }}</strong><div class="connection-tags"><span class="tag-network">{{ connection.network }}</span><span class="tag-inbound">{{ connection.inbound }}</span><span class="tag-process">{{ connection.process }}</span><span class="tag-policy">{{ connection.policy }}</span><span class="tag-age">{{ connectionAge(connection.startedAt) }}</span><span v-if="connection.uploadSpeed || connection.downloadSpeed" class="tag-speed">↑{{ formatRate(connection.uploadSpeed) }} ↓{{ formatRate(connection.downloadSpeed) }}</span></div></div><button title="关闭连接" aria-label="关闭连接" @click="closeConnection(connection.id)"><svg class="row-icon close-icon"><use href="#icon-connection-close" /></svg></button></div>
            <div v-if="virtualConnectionBottom" aria-hidden="true" :style="{ height: `${virtualConnectionBottom}px` }" />
          </div>
        </section>

        <section v-else-if="activePage === 'settings'" class="screen-page narrow-page">
          <div class="screen-heading"><div><h2>设置</h2><p>应用外观和运行偏好</p></div></div>
          <div class="simple-settings">
            <button class="simple-setting setting-control" @click="toggleRememberPage"><div><strong>启动时打开上次页面</strong><small>记住上次使用的导航位置</small></div><span class="switch" :class="{ on: rememberPageEnabled }"><i /></span></button>
            <button class="simple-setting setting-control" @click="toggleTrayOnClose"><div><strong>状态栏常驻</strong><small>关闭窗口后隐藏到系统托盘，核心继续运行</small></div><span class="switch" :class="{ on: trayOnCloseEnabled }"><i /></span></button>
          </div>
        </section>

        <section v-else class="empty-page">
          <div class="empty-mark"><svg class="empty-icon"><use :href="`#icon-${navItems.find((item) => item.id === activePage)?.icon}`" /></svg></div>
          <h2>{{ pageTitle }}</h2>
          <p>界面已预留，后续接入 Mihomo 双实例管理。</p>
        </section>
      </main>
    </div>
    <div v-if="showSubscriptionDialog" class="modal-backdrop" @click.self="closeSubscriptionDialog">
      <form class="modal-card" @submit.prevent="submitSubscription">
        <div class="modal-heading"><div><h2>添加订阅</h2><p>保存后下载配置，并应用到当前核心</p></div><button class="modal-close" type="button" aria-label="关闭" @click="closeSubscriptionDialog"><svg><use href="#icon-close" /></svg></button></div>
        <label class="modal-field"><span>配置名称</span><input v-model="subscriptionName" required maxlength="80" placeholder="例如：游戏订阅" /></label>
        <label class="modal-field"><span>订阅地址</span><input v-model="subscriptionUrl" required type="url" placeholder="https://example.com/subscribe" /></label>
        <div v-if="profileError" class="modal-error">{{ profileError }}</div>
        <div class="modal-actions"><button class="page-button secondary" type="button" @click="closeSubscriptionDialog">取消</button><button class="page-button" type="submit" :disabled="profileStatus === 'loading' || coreMutationPending">{{ profileStatus === 'loading' ? '添加中…' : '添加并启用' }}</button></div>
      </form>
    </div>
    <div v-if="showCoreDialog" class="modal-backdrop" @click.self="!coreCreationPending && (showCoreDialog = false)">
      <form class="modal-card" @submit.prevent="submitCore">
        <div class="modal-heading"><div><h2>创建核心</h2><p>{{ coreCreationPending ? '正在启动核心，首次启动可能需要下载 MMDB…' : '端口和密钥将自动分配' }}</p></div><button class="modal-close" type="button" :disabled="coreCreationPending" @click="showCoreDialog = false"><svg><use href="#icon-close" /></svg></button></div>
        <label class="modal-field"><span>核心名称</span><input v-model="coreName" required maxlength="80" :disabled="coreCreationPending" placeholder="例如：默认核心" /></label>
        <div class="modal-field"><span>导入配置</span><div class="core-profile-options"><button v-for="profile in profiles" :key="profile.id" type="button" :disabled="coreCreationPending" :class="{ active: coreProfileId === profile.id }" @click="coreProfileId = profile.id">{{ profile.name }}</button></div></div>
        <div class="modal-actions"><button class="page-button secondary" type="button" :disabled="coreCreationPending" @click="showCoreDialog = false">取消</button><button class="page-button" type="submit" :disabled="!coreProfileId || coreCreationPending">{{ coreCreationPending ? '启动中…' : '创建并启动' }}</button></div>
      </form>
    </div>
    <div v-if="editingProfileId" class="modal-backdrop" @click.self="editingProfileId = ''">
      <form class="modal-card profile-editor-card" @submit.prevent="submitProfileEdit">
        <div class="modal-heading"><div><h2>编辑配置</h2><p>修改名称和 YAML 内容，保存后重载关联核心</p></div><button class="modal-close" type="button" @click="editingProfileId = ''"><svg><use href="#icon-close" /></svg></button></div>
        <label class="modal-field"><span>配置名称</span><input v-model="editingProfileName" required maxlength="80" /></label>
        <label class="modal-field"><span>YAML 内容</span><YamlEditor v-model="editingProfileContent" /></label>
        <div class="modal-actions"><button class="page-button secondary" type="button" @click="editingProfileId = ''">取消</button><button class="page-button" type="submit">保存并重载</button></div>
      </form>
    </div>
    <div v-if="contextMenu" class="context-menu" :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }" @pointerdown.stop>
      <button class="context-delete" @click="requestDeleteFromContextMenu">删除</button>
    </div>
    <div v-if="deleteTarget" class="modal-backdrop" @click.self="!deletingTarget && (deleteTarget = null)">
      <div class="modal-card delete-confirm-card" role="dialog" aria-modal="true">
        <div class="modal-heading"><div><h2>删除{{ deleteTarget.kind === 'core' ? '核心' : '配置' }}</h2><p>此操作会删除本地数据，无法撤销。</p></div><button class="modal-close" :disabled="deletingTarget" @click="deleteTarget = null"><svg><use href="#icon-close" /></svg></button></div>
        <p class="delete-target-name">{{ deleteTarget.name }}</p>
        <p v-if="deleteTarget.kind === 'profile'" class="delete-note">如果配置仍被某个核心使用，后端会拒绝删除。</p>
        <div class="modal-actions"><button class="page-button secondary" :disabled="deletingTarget" @click="deleteTarget = null">取消</button><button class="page-button danger-button" :disabled="deletingTarget" @click="confirmDelete">{{ deletingTarget ? '删除中…' : '确认删除' }}</button></div>
      </div>
    </div>
    <input ref="profileFileInput" class="visually-hidden-input" type="file" accept=".yaml,.yml" @change="handleProfileFile" />
    <transition name="toast">
      <div v-if="toastMessage" class="toast-message" role="status">{{ toastMessage }}</div>
    </transition>
  </div>
</template>
