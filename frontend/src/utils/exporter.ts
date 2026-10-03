/**
 * 行程导出工具：html2canvas 截图 + jsPDF A4 多页切割。
 * 从 Result.vue 抽出为纯函数，便于独立测试与复用。
 */
import html2canvas from 'html2canvas'
import { jsPDF } from 'jspdf'

/** 把容器节点截为 2x 白底画布。 */
export async function captureElement(el: HTMLElement): Promise<HTMLCanvasElement> {
  return html2canvas(el, {
    scale: 2,
    useCORS: true, // 允许加载 Unsplash 跨域图片
    backgroundColor: '#ffffff',
  })
}

/** 截图保存为长图 PNG 并触发下载。 */
export async function exportElementAsPng(el: HTMLElement, filename: string): Promise<void> {
  const canvas = await captureElement(el)
  const link = document.createElement('a')
  link.download = filename
  link.href = canvas.toDataURL('image/png')
  link.click()
}

/**
 * 截图按 A4 纵向多页切割导出 PDF：
 * 图片等宽铺满页宽，逐页下移 pageH，直到覆盖完整高度。
 */
export async function exportElementAsPdf(el: HTMLElement, filename: string): Promise<void> {
  const canvas = await captureElement(el)
  const pdf = new jsPDF('p', 'mm', 'a4')
  const pageW = 210
  const pageH = 297
  const imgH = (pageW * canvas.height) / canvas.width
  const img = canvas.toDataURL('image/jpeg', 0.92)
  let remaining = imgH
  let position = 0
  pdf.addImage(img, 'JPEG', 0, position, pageW, imgH)
  remaining -= pageH
  while (remaining > 0) {
    pdf.addPage()
    position -= pageH
    pdf.addImage(img, 'JPEG', 0, position, pageW, imgH)
    remaining -= pageH
  }
  pdf.save(filename)
}
