import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  // 生产构建剔除 console.log（保留 error/warn 便于线上排查）
  esbuild: {
    pure: ['console.log'],
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    rollupOptions: {
      output: {
        // 大依赖分包：地图/导出/组件库各自独立 chunk，业务代码改动不影响其缓存
        manualChunks: {
          vendor: ['vue', 'vue-router', 'axios'],
          antd: ['ant-design-vue', '@ant-design/icons-vue'],
          amap: ['@amap/amap-jsapi-loader'],
          exporter: ['html2canvas', 'jspdf'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      // 前端所有 /api 请求转发到 FastAPI 后端，避免跨域
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
  },
})
