<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { fetchLatestRelease, isVersionNewer, type GithubRelease } from '../services/releases'
import { APP_VERSION } from '../version'

type CheckStatus = 'checking' | 'available' | 'current' | 'error'

const releasesUrl = 'https://github.com/Himpq/ohmyclash/releases'
const latestRelease = ref<GithubRelease | null>(null)
const checkStatus = ref<CheckStatus>('checking')
const errorMessage = ref('')
const checkedAt = ref<Date | null>(null)
let checkController: AbortController | null = null

const statusMessage = computed(() => {
  if (checkStatus.value === 'checking') return '正在检查 GitHub Releases…'
  if (checkStatus.value === 'available') return '发现新版本'
  if (checkStatus.value === 'current') return '当前已是最新版本'
  return '版本检查失败'
})

const formattedCheckedAt = computed(() => checkedAt.value
  ? new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(checkedAt.value)
  : '')

async function checkForUpdates() {
  checkController?.abort()
  const controller = new AbortController()
  checkController = controller
  let requestTimedOut = false
  const timeoutId = window.setTimeout(() => {
    requestTimedOut = true
    controller.abort()
  }, 15000)
  checkStatus.value = 'checking'
  errorMessage.value = ''

  try {
    const release = await fetchLatestRelease(controller.signal)
    latestRelease.value = release
    checkStatus.value = isVersionNewer(release.tag_name, APP_VERSION) ? 'available' : 'current'
  } catch (error) {
    if (controller.signal.aborted && !requestTimedOut) return
    console.error('OhMyClash 版本更新检查失败', error)
    checkStatus.value = 'error'
    errorMessage.value = requestTimedOut
      ? '连接 GitHub 超时，请检查网络后重试。'
      : error instanceof Error ? error.message : '未知错误'
  } finally {
    window.clearTimeout(timeoutId)
    if (checkController === controller && (!controller.signal.aborted || requestTimedOut)) checkedAt.value = new Date()
  }
}

onMounted(() => void checkForUpdates())
onBeforeUnmount(() => checkController?.abort())
</script>

<template>
  <section class="screen-page about-page">
    <div class="screen-heading">
      <div>
        <h2>关于</h2>
        <p>OhMyClash 桌面客户端</p>
      </div>
    </div>

    <article class="about-card">
      <div class="about-brand">
        <div>
          <h3>OhMyClash</h3>
          <p>多核心 Mihomo 管理工具</p>
        </div>
      </div>

      <dl class="about-versions">
        <div class="about-version-row">
          <dt>当前版本</dt>
          <dd>v{{ APP_VERSION }}</dd>
        </div>
        <div class="about-version-row">
          <dt>最新版本</dt>
          <dd>{{ latestRelease?.tag_name ?? '—' }}</dd>
        </div>
        <div class="about-update-state" :class="`state-${checkStatus}`" role="status" aria-live="polite">
          <span class="about-state-dot" />
          <span>{{ statusMessage }}</span>
          <small v-if="formattedCheckedAt">检查于 {{ formattedCheckedAt }}</small>
        </div>
        <p v-if="errorMessage" class="about-error">{{ errorMessage }}</p>
        <div v-if="checkStatus === 'available' && latestRelease" class="about-update-note">
          新版本 {{ latestRelease.tag_name }} 已发布。
          <a :href="latestRelease.html_url" target="_blank" rel="noopener noreferrer">查看版本详情</a>
        </div>
        <div v-if="latestRelease?.published_at" class="about-version-row about-published-row">
          <dt>发布时间</dt>
          <dd>{{ new Date(latestRelease.published_at).toLocaleDateString('zh-CN') }}</dd>
        </div>
      </dl>

      <div class="about-actions">
        <button class="page-button" :disabled="checkStatus === 'checking'" @click="checkForUpdates">
          {{ checkStatus === 'checking' ? '检查中…' : '检查更新' }}
        </button>
        <a class="about-releases-link" :href="releasesUrl" target="_blank" rel="noopener noreferrer">GitHub Releases</a>
      </div>
    </article>
  </section>
</template>

<style scoped>
.about-page { max-width: 820px; }
.about-card { padding: 24px; background: #333242; border: 1px solid #42414f; border-radius: 5px; }
.about-brand { padding-bottom: 22px; border-bottom: 1px solid #464553; }
.about-brand h3 { margin: 0; color: #f1eff3; font-size: 18px; font-weight: 450; }
.about-brand p { margin: 5px 0 0; color: #9c9aa7; font-size: 12px; }
.about-versions { margin: 14px 0 0; }
.about-version-row { display: flex; min-height: 37px; align-items: center; justify-content: space-between; gap: 16px; color: #bdbbc6; font-size: 12px; }
.about-version-row dd { margin: 0; color: #efedf1; font-family: Consolas, "Courier New", monospace; }
.about-update-state { display: flex; min-height: 36px; align-items: center; gap: 8px; color: #c5c3cc; font-size: 12px; }
.about-state-dot { width: 8px; height: 8px; flex: 0 0 8px; background: #908e9b; border-radius: 50%; }
.state-available .about-state-dot { background: #e8b354; }
.state-current .about-state-dot { background: #41bb87; }
.state-error .about-state-dot { background: #e16a70; }
.about-update-state small { margin-left: auto; color: #8e8c98; font-size: 10px; }
.about-error { margin: 1px 0 10px 16px; color: #e69498; font-size: 11px; line-height: 1.5; }
.about-update-note { margin: 0 0 10px 16px; color: #e3bf7e; font-size: 11px; line-height: 1.6; }
.about-update-note a, .about-releases-link { color: #91b2ed; text-decoration: none; }
.about-update-note a:hover, .about-releases-link:hover { text-decoration: underline; }
.about-published-row { border-top: 1px solid #464553; }
.about-actions { display: flex; align-items: center; gap: 15px; margin-top: 19px; }
.about-releases-link { font-size: 11px; }
@media (max-width: 650px) {
  .about-card { padding: 17px; }
  .about-update-state { flex-wrap: wrap; }
  .about-update-state small { width: 100%; margin: 0 0 0 16px; }
}
</style>
