<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{ modelValue: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const textarea = ref<HTMLTextAreaElement | null>(null)
const code = ref<HTMLElement | null>(null)
const gutter = ref<HTMLElement | null>(null)

const lineNumbers = computed(() => Array.from({ length: Math.max(1, props.modelValue.split('\n').length) }, (_, index) => index + 1))
const highlighted = computed(() => props.modelValue.split('\n').map(highlightYamlLine).join('\n') + '\n')

function escapeHtml(value: string) {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
}

function findOutsideQuotes(value: string, target: string) {
  let quote = ''
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index]
    if ((char === '"' || char === "'") && value[index - 1] !== '\\') quote = quote === char ? '' : quote || char
    if (!quote && char === target) return index
  }
  return -1
}

function colorValues(value: string) {
  const matcher = /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b(?:true|false|null|yes|no|on|off)\b|-?\b\d+(?:\.\d+)?\b)/gi
  let output = ''
  let cursor = 0
  for (const match of value.matchAll(matcher)) {
    const index = match.index ?? 0
    output += escapeHtml(value.slice(cursor, index))
    const token = match[0]
    const kind = token.startsWith('"') || token.startsWith("'") ? 'string' : /^(?:true|false|null|yes|no|on|off)$/i.test(token) ? 'literal' : 'number'
    output += `<span class="yaml-${kind}">${escapeHtml(token)}</span>`
    cursor = index + token.length
  }
  return output + escapeHtml(value.slice(cursor))
}

function highlightYamlLine(line: string) {
  const commentAt = findOutsideQuotes(line, '#')
  const body = commentAt >= 0 ? line.slice(0, commentAt) : line
  const comment = commentAt >= 0 ? line.slice(commentAt) : ''
  const colonAt = findOutsideQuotes(body, ':')
  let output = ''
  if (colonAt >= 0 && !/^\s*(?:https?|socks):\/\//i.test(body)) {
    const key = body.slice(0, colonAt)
    output = `<span class="yaml-key">${escapeHtml(key)}</span><span class="yaml-punctuation">:</span>${colorValues(body.slice(colonAt + 1))}`
  } else {
    output = colorValues(body)
  }
  if (comment) output += `<span class="yaml-comment">${escapeHtml(comment)}</span>`
  return output || ' '
}

function updateValue(event: Event) {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}

function syncScroll() {
  const input = textarea.value
  if (!input) return
  if (code.value) code.value.style.transform = `translate(${-input.scrollLeft}px, ${-input.scrollTop}px)`
  if (gutter.value) gutter.value.style.transform = `translateY(${-input.scrollTop}px)`
}
</script>

<template>
  <div class="yaml-editor">
    <div class="yaml-gutter-viewport" aria-hidden="true"><div ref="gutter" class="yaml-gutter"><span v-for="line in lineNumbers" :key="line">{{ line }}</span></div></div>
    <pre class="yaml-highlight" aria-hidden="true"><code ref="code" v-html="highlighted" /></pre>
    <textarea ref="textarea" :value="modelValue" aria-label="YAML 内容" spellcheck="false" @input="updateValue" @scroll="syncScroll" />
  </div>
</template>

<style scoped>
.yaml-editor {
  position: relative;
  width: 100%;
  height: 310px;
  overflow: hidden;
  background: #292838;
  border: 1px solid #51505e;
  border-radius: 4px;
  --gutter-width: 42px;
  --line-height: 17px;
}
.yaml-editor:focus-within { border-color: #6a8fd0; }
.yaml-editor textarea, .yaml-highlight {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  margin: 0;
  padding: 9px 10px 9px calc(var(--gutter-width) + 9px);
  border: 0;
  outline: 0;
  font-family: Consolas, "Courier New", monospace;
  font-size: 12px;
  line-height: var(--line-height);
  tab-size: 2;
  white-space: pre;
}
.yaml-highlight { overflow: hidden; color: #d7d5dc; pointer-events: none; }
.yaml-highlight code { display: block; width: max-content; min-width: 100%; }
:deep(.yaml-highlight span) { display: inline; }
.yaml-editor textarea {
  z-index: 2;
  resize: none;
  overflow: auto;
  color: transparent;
  background: transparent;
  caret-color: #fff;
  -webkit-text-fill-color: transparent;
  scrollbar-color: #666573 #292838;
  scrollbar-width: thin;
}
.yaml-editor textarea::selection { background: rgba(90, 118, 169, .55); }
.yaml-gutter-viewport { position: absolute; inset: 0 auto 0 0; z-index: 1; width: var(--gutter-width); overflow: hidden; background: #302f3e; border-right: 1px solid #464553; pointer-events: none; }
.yaml-gutter { padding-top: 9px; }
.yaml-gutter span { display: block; height: var(--line-height); padding-right: 8px; color: #777582; font-family: Consolas, "Courier New", monospace; font-size: 11px; line-height: var(--line-height); text-align: right; }
:deep(.yaml-key) { color: #79b8ff; }
:deep(.yaml-punctuation) { color: #a9a7b1; }
:deep(.yaml-string) { color: #e5c07b; }
:deep(.yaml-number) { color: #c792ea; }
:deep(.yaml-literal) { color: #56b6c2; }
:deep(.yaml-comment) { color: #6f946a; font-style: italic; }
</style>
