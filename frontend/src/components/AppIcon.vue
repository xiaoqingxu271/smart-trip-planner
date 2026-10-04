<script setup lang="ts">
import { computed } from 'vue'

/**
 * 全局线性图标：Lucide（ISC 许可）本地 SVG 资产，见 src/assets/icons/。
 * SVG 为 1em + currentColor 描边，尺寸由 size 设定，颜色默认继承文字色，
 * 传入 color 时以内联样式固定（html2canvas 导出时也能正确着色）。
 */
const raws = import.meta.glob<string>('@/assets/icons/*.svg', {
  query: '?raw',
  import: 'default',
  eager: true,
})

const props = withDefaults(
  defineProps<{
    name: string
    size?: number
    color?: string
    filled?: boolean
  }>(),
  { size: 16 },
)

const svg = computed(() => {
  const raw = raws[`/src/assets/icons/${props.name}.svg`]
  if (!raw) return ''
  let s = raw
    .replaceAll('width="1em"', `width="${props.size}"`)
    .replaceAll('height="1em"', `height="${props.size}"`)
  if (props.filled) {
    s = s.replace('<g fill="none"', '<g fill="currentColor"')
  }
  return s
})

const style = computed(() => (props.color ? { color: props.color } : undefined))
</script>

<template>
  <span class="app-icon" :style="style" v-html="svg" />
</template>

<style scoped>
.app-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  line-height: 1;
  vertical-align: -0.15em;
}

.app-icon :deep(svg) {
  display: block;
}
</style>
