<script setup lang="ts">
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import type { ThemeConfig } from 'ant-design-vue/es/config-provider/context'

// 全局主题令牌：与 style.css 的 "Forest & Cream" 设计系统保持一致
const theme: ThemeConfig = {
  token: {
    colorPrimary: '#2f7254',
    colorInfo: '#2f7254',
    colorLink: '#2f7254',
    colorSuccess: '#3f8765',
    colorWarning: '#c97f22',
    colorError: '#c25644',
    colorTextBase: '#1c2b24',
    colorBgLayout: '#f3f6f1',
    colorBorder: '#d8e0d6',
    colorBorderSecondary: '#e4e9e2',
    borderRadius: 10,
    controlHeight: 38,
    fontFamily:
      "'PingFang SC', 'Microsoft YaHei', 'Helvetica Neue', Arial, sans-serif",
  },
}
</script>

<template>
  <a-config-provider :locale="zhCN" :theme="theme">
    <router-view />
  </a-config-provider>
</template>
