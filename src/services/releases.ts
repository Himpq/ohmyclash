export type GithubRelease = {
  tag_name: string
  html_url: string
  published_at: string | null
}

const LATEST_RELEASE_URL = 'https://api.github.com/repos/Himpq/ohmyclash/releases/latest'

export async function fetchLatestRelease(signal?: AbortSignal): Promise<GithubRelease> {
  const response = await fetch(LATEST_RELEASE_URL, {
    headers: { Accept: 'application/vnd.github+json' },
    signal,
  })

  if (response.status === 404) {
    throw new Error('找不到正式版 Release；也可能是仓库暂时不可访问')
  }
  if (!response.ok) {
    throw new Error(`GitHub Releases API 请求失败（HTTP ${response.status}）`)
  }

  const payload: unknown = await response.json()
  if (!isGithubRelease(payload)) {
    throw new Error('GitHub 返回的 Release 数据格式无效')
  }

  return payload
}

function isGithubRelease(value: unknown): value is GithubRelease {
  if (!value || typeof value !== 'object') return false
  const release = value as Record<string, unknown>
  if (typeof release.tag_name !== 'string'
    || typeof release.html_url !== 'string'
    || (typeof release.published_at !== 'string' && release.published_at !== null)) return false

  try {
    const releaseUrl = new URL(release.html_url)
    return releaseUrl.origin === 'https://github.com'
      && releaseUrl.pathname.startsWith('/Himpq/ohmyclash/releases/tag/')
  } catch {
    return false
  }
}

export function isVersionNewer(candidate: string, current: string): boolean {
  const candidateVersion = parseVersion(candidate)
  const currentVersion = parseVersion(current)

  for (let index = 0; index < 3; index += 1) {
    if (candidateVersion.core[index] !== currentVersion.core[index]) {
      return candidateVersion.core[index] > currentVersion.core[index]
    }
  }

  if (candidateVersion.prerelease === null) return currentVersion.prerelease !== null
  if (currentVersion.prerelease === null) return false

  const count = Math.max(candidateVersion.prerelease.length, currentVersion.prerelease.length)
  for (let index = 0; index < count; index += 1) {
    const candidatePart = candidateVersion.prerelease[index]
    const currentPart = currentVersion.prerelease[index]
    if (candidatePart === undefined) return false
    if (currentPart === undefined) return true
    if (candidatePart === currentPart) continue

    const candidateNumeric = /^\d+$/.test(candidatePart)
    const currentNumeric = /^\d+$/.test(currentPart)
    if (candidateNumeric && currentNumeric) return Number(candidatePart) > Number(currentPart)
    if (candidateNumeric !== currentNumeric) return !candidateNumeric
    return candidatePart > currentPart
  }

  return false
}

function parseVersion(value: string): { core: [number, number, number]; prerelease: string[] | null } {
  const match = value.trim().match(/^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/i)
  if (!match) throw new Error(`无法识别版本号：${value}`)

  return {
    core: [Number(match[1]), Number(match[2]), Number(match[3])],
    prerelease: match[4]?.split('.') ?? null,
  }
}
