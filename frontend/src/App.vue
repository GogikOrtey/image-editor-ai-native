/**
 * Image Editor MVP: upload, mask painting, inpaint generation, and native export.
 * The root view owns the image-coordinate Uint8ClampedArray mask and canvas viewport.
 * API calls use multipart FormData with provider settings persisted in localStorage.
 * Keep drawing coordinates in source-image space so zoom and pan never alter mask data.
 */
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  Blend, Brush, Check, Cpu, Download, Eraser, Eye, EyeOff, FileImage, Hand,
  ImagePlus, LoaderCircle, Lasso, Minus, MousePointer2, Pencil,
  Plus, Redo2, RotateCcw, Sparkles, Trash2, Undo2, Upload, WandSparkles,
  X,
} from 'lucide-vue-next'

type Tool = 'select' | 'brush' | 'rectangle' | 'lasso' | 'eraser'
type Point = { x: number; y: number }
type HistoryItem = { id: number; url: string; label: string; createdAt: string }
type UndoSnapshot = { mask: Uint8ClampedArray; imageUrl: string; imageName: string; sourceBlob: Blob | null }

/** One catalogue entry as returned by GET /api/models. */
type ModelEntry = { id: string; name: string | null; aliases: string[] | null }

/** A picker row: one provider with its lighter and heavier model side by side. */
type ModelGroup = { key: string; provider: string; family: string; tiers: ModelEntry[] }

const MODEL_LABELS: Record<string, { provider: string; family: string; tier: string }> = {
  'openai/gpt-image-2.5-flare': { provider: 'OpenAI', family: 'GPT Image 2.5', tier: 'Flash' },
  'openai/gpt-image-2.5-sunburst': { provider: 'OpenAI', family: 'GPT Image 2.5', tier: 'Pro' },
  'google/gemini-3.1-flash-image-preview': { provider: 'Google', family: 'Nano Banana', tier: 'Flash' },
  'google/gemini-3-pro-image': { provider: 'Google', family: 'Nano Banana', tier: 'Pro' },
  'qwen/qwen-image-3.0': { provider: 'Qwen', family: 'Qwen Image 3.0', tier: 'Flash' },
  'qwen/qwen-image-3.0-pro': { provider: 'Qwen', family: 'Qwen Image 3.0', tier: 'Pro' },
}

const MODEL_NOTES: Record<string, string> = {
  'openai/gpt-image-2.5-flare': 'Быстрая и недорогая, хорошо держит композицию',
  'openai/gpt-image-2.5-sunburst': 'Точнее следует сложным инструкциям',
  'google/gemini-3.1-flash-image-preview': 'Быстрая правка и понимание объектов',
  'google/gemini-3-pro-image': 'Лучшая детализация и свет',
  'qwen/qwen-image-3.0': 'Сильная работа с текстом в кадре',
  'qwen/qwen-image-3.0-pro': 'Максимальное качество, дороже остальных',
}

type Settings = { model: string; prompt: string }

const canvasRef = ref<HTMLCanvasElement | null>(null)
const workspaceRef = ref<HTMLDivElement | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const image = ref<HTMLImageElement | null>(null)
const imageUrl = ref('')
const sourceBlob = ref<Blob | null>(null)
const imageName = ref('')
const imageSize = ref({ width: 0, height: 0 })
const mask = ref<Uint8ClampedArray | null>(null)
const hasMask = ref(false)
const tool = ref<Tool>('select')
const brushSize = ref(32)
const zoom = ref(1)
const pan = ref({ x: 0, y: 0 })
const maskVisible = ref(true)
const overlayOpacity = ref(0.42)
const isDraggingFile = ref(false)
const isGenerating = ref(false)
const status = ref('Ready')
const errorMessage = ref('')
const prompt = ref('')
const model = ref('openai/gpt-image-2.5-flare')
const catalogModels = ref<ModelEntry[]>([])
const catalogState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const catalogError = ref('')
const generationHistory = ref<HistoryItem[]>([])
const activeHistory = ref(-1)
const spacePressed = ref(false)
const undoStack = ref<UndoSnapshot[]>([])
const redoStack = ref<UndoSnapshot[]>([])
const showWelcome = computed(() => !image.value)
const canGenerate = computed(() => Boolean(image.value && hasMask.value && prompt.value.trim() && !isGenerating.value && model.value))
const selectedTool = computed(() => tool.value)
const activeModel = computed(() => catalogModels.value.find((entry) => entry.id === model.value))
const modelGroups = computed<ModelGroup[]>(() => {
  const groups: ModelGroup[] = []
  const order = ['OpenAI', 'Google', 'Qwen']
  const sorted = [...catalogModels.value].sort((left, right) => {
    const leftLabel = modelMeta(left)
    const rightLabel = modelMeta(right)
    const providerDelta = order.indexOf(leftLabel.provider) - order.indexOf(rightLabel.provider)
    return providerDelta !== 0 ? providerDelta : leftLabel.tier.localeCompare(rightLabel.tier)
  })
  for (const entry of sorted) {
    const meta = modelMeta(entry)
    const existing = groups.find((group) => group.key === `${meta.provider}/${meta.family}`)
    if (existing) existing.tiers.push(entry)
    else groups.push({ key: `${meta.provider}/${meta.family}`, provider: meta.provider, family: meta.family, tiers: [entry] })
  }
  return groups
})

let resizeObserver: ResizeObserver | null = null
let maskCanvas: HTMLCanvasElement | null = null
let maskContext: CanvasRenderingContext2D | null = null
let isDrawing = false
let isPanning = false
let lastPoint: Point | null = null
let startPoint: Point | null = null
let lassoPoints: Point[] = []
let panStart = { x: 0, y: 0 }
let panOrigin = { x: 0, y: 0 }
let idCounter = 1

const toolItems: Array<{ id: Tool; label: string; icon: typeof Brush }> = [
  { id: 'select', label: 'Move', icon: MousePointer2 },
  { id: 'brush', label: 'Brush', icon: Brush },
  { id: 'rectangle', label: 'Rectangle', icon: Pencil },
  { id: 'lasso', label: 'Lasso', icon: Lasso },
  { id: 'eraser', label: 'Eraser', icon: Eraser },
]

/** Printable name for a catalogue entry, with a readable fallback for unknown ids. */
function modelMeta(entry: ModelEntry): { provider: string; family: string; tier: string; note: string } {
  const known = MODEL_LABELS[entry.id]
  if (known) return { ...known, note: MODEL_NOTES[entry.id] || '' }
  const [provider = 'Model', slug = entry.id] = entry.id.split('/')
  return {
    provider: provider.charAt(0).toUpperCase() + provider.slice(1),
    family: entry.name || slug,
    tier: 'Model',
    note: '',
  }
}

async function loadCatalog() {
  catalogState.value = 'loading'
  catalogError.value = ''
  try {
    const response = await fetch('/api/models')
    if (!response.ok) {
      const body = await response.json().catch(() => ({})) as { detail?: string }
      throw new Error(body.detail || `Catalogue unavailable (${response.status})`)
    }
    const body = await response.json() as { models?: ModelEntry[] }
    const models = (body.models || []).filter((entry) => entry?.id)
    if (!models.length) throw new Error('Catalogue returned no image models')
    catalogModels.value = models
    catalogState.value = 'ready'
    if (!models.some((entry) => entry.id === model.value)) model.value = models[0].id
  } catch (error) {
    catalogState.value = 'error'
    catalogError.value = error instanceof Error ? error.message : 'Could not load models'
  }
}

function selectModel(id: string) {
  model.value = id
  const meta = MODEL_LABELS[id]
  if (meta) setStatus(`${meta.provider} ${meta.family} ${meta.tier} selected`)
}

function saveSettings() {
  localStorage.setItem('image-editor-settings', JSON.stringify({ model: model.value, prompt: prompt.value }))
}

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem('image-editor-settings') || '{}') as Partial<Settings>
    model.value = saved.model || 'openai/gpt-image-2.5-flare'
    prompt.value = saved.prompt || ''
  } catch {
    status.value = 'Settings reset'
  }
}

watch([model, prompt], saveSettings)

function setStatus(message: string) {
  status.value = message
  errorMessage.value = ''
}

function makeMask(width: number, height: number, values?: Uint8ClampedArray) {
  mask.value = values ? new Uint8ClampedArray(values) : new Uint8ClampedArray(width * height)
  hasMask.value = Boolean(values?.some((value) => value > 0))
  maskCanvas = document.createElement('canvas')
  maskCanvas.width = width
  maskCanvas.height = height
  maskContext = maskCanvas.getContext('2d')
  render()
}

function resetViewport() {
  const canvas = canvasRef.value
  const currentImage = image.value
  if (!canvas || !currentImage) {
    zoom.value = 1
    pan.value = { x: 0, y: 0 }
    return
  }
  const availableWidth = Math.max(1, canvas.clientWidth - 96)
  const availableHeight = Math.max(1, canvas.clientHeight - 120)
  zoom.value = Math.min(1, availableWidth / currentImage.naturalWidth, availableHeight / currentImage.naturalHeight)
  pan.value = { x: 0, y: 0 }
}

function loadImageFile(file: File) {
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
    errorMessage.value = 'Use a PNG, JPG, or WebP image.'
    return
  }
  const nextUrl = URL.createObjectURL(file)
  const nextImage = new Image()
  nextImage.onload = () => {
    if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
    imageUrl.value = nextUrl
    sourceBlob.value = file
    imageName.value = file.name
    image.value = nextImage
    imageSize.value = { width: nextImage.naturalWidth, height: nextImage.naturalHeight }
    makeMask(nextImage.naturalWidth, nextImage.naturalHeight)
    undoStack.value = []
    redoStack.value = []
    generationHistory.value = []
    activeHistory.value = -1
    resetViewport()
    setStatus(`${nextImage.naturalWidth} x ${nextImage.naturalHeight}`)
    nextTick(render)
  }
  nextImage.onerror = () => {
    URL.revokeObjectURL(nextUrl)
    errorMessage.value = 'Could not read that image.'
  }
  nextImage.src = nextUrl
}

function onFileInput(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (file) loadImageFile(file)
  ;(event.target as HTMLInputElement).value = ''
}

function onDrop(event: DragEvent) {
  event.preventDefault()
  isDraggingFile.value = false
  const file = event.dataTransfer?.files?.[0]
  if (file) loadImageFile(file)
}

function getCanvasPoint(event: PointerEvent | WheelEvent): Point {
  const rect = canvasRef.value!.getBoundingClientRect()
  return { x: event.clientX - rect.left, y: event.clientY - rect.top }
}

function imageLayout() {
  const canvas = canvasRef.value
  const currentImage = image.value
  if (!canvas || !currentImage) return { x: 0, y: 0, width: 0, height: 0 }
  const width = currentImage.naturalWidth * zoom.value
  const height = currentImage.naturalHeight * zoom.value
  return { x: canvas.clientWidth / 2 - width / 2 + pan.value.x, y: canvas.clientHeight / 2 - height / 2 + pan.value.y, width, height }
}

function toImagePoint(point: Point): Point | null {
  const layout = imageLayout()
  if (!layout.width || point.x < layout.x || point.y < layout.y || point.x > layout.x + layout.width || point.y > layout.y + layout.height) return null
  return { x: (point.x - layout.x) / zoom.value, y: (point.y - layout.y) / zoom.value }
}

function captureSnapshot(): UndoSnapshot | null {
  if (!mask.value) return null
  return { mask: new Uint8ClampedArray(mask.value), imageUrl: imageUrl.value, imageName: imageName.value, sourceBlob: sourceBlob.value }
}

function applySnapshot(snapshot: UndoSnapshot) {
  mask.value = new Uint8ClampedArray(snapshot.mask)
  imageName.value = snapshot.imageName
  sourceBlob.value = snapshot.sourceBlob
  if (imageUrl.value !== snapshot.imageUrl) {
    imageUrl.value = snapshot.imageUrl
    const restored = new Image()
    restored.onload = () => {
      image.value = restored
      imageSize.value = { width: restored.naturalWidth, height: restored.naturalHeight }
      makeMask(restored.naturalWidth, restored.naturalHeight, snapshot.mask)
      resetViewport()
    }
    restored.src = snapshot.imageUrl
  } else {
    syncMaskCanvas()
  }
}

function pushUndo() {
  const snapshot = captureSnapshot()
  if (!snapshot) return
  undoStack.value.push(snapshot)
  if (undoStack.value.length > 20) undoStack.value.shift()
  redoStack.value = []
}

function undo() {
  if (isGenerating.value || !mask.value || !undoStack.value.length) return
  const current = captureSnapshot()
  const previous = undoStack.value.pop()
  if (!current || !previous) return
  redoStack.value.push(current)
  applySnapshot(previous)
  setStatus('Action undone')
}

function redo() {
  if (isGenerating.value || !mask.value || !redoStack.value.length) return
  const current = captureSnapshot()
  const next = redoStack.value.pop()
  if (!current || !next) return
  undoStack.value.push(current)
  applySnapshot(next)
  setStatus('Action redone')
}

function syncMaskCanvas() {
  if (!mask.value || !maskCanvas || !maskContext) return
  const pixels = new Uint8ClampedArray(mask.value.length * 4)
  for (let i = 0; i < mask.value.length; i += 1) {
    pixels[i * 4] = 37
    pixels[i * 4 + 1] = 99
    pixels[i * 4 + 2] = 235
    pixels[i * 4 + 3] = mask.value[i]
  }
  hasMask.value = mask.value.some((value) => value > 0)
  maskContext.putImageData(new ImageData(pixels, imageSize.value.width, imageSize.value.height), 0, 0)
  render()
}

function paintCircle(point: Point, radius: number, value: number) {
  if (!mask.value) return
  const { width, height } = imageSize.value
  const left = Math.max(0, Math.floor(point.x - radius))
  const right = Math.min(width - 1, Math.ceil(point.x + radius))
  const top = Math.max(0, Math.floor(point.y - radius))
  const bottom = Math.min(height - 1, Math.ceil(point.y + radius))
  for (let y = top; y <= bottom; y += 1) {
    for (let x = left; x <= right; x += 1) {
      if ((x - point.x) ** 2 + (y - point.y) ** 2 <= radius ** 2) mask.value[y * width + x] = value
    }
  }
}

function paintLine(from: Point, to: Point, value: number) {
  const distance = Math.hypot(to.x - from.x, to.y - from.y)
  const steps = Math.max(1, Math.ceil(distance / Math.max(1, brushSize.value / 3)))
  const radius = brushSize.value / 2
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps
    paintCircle({ x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t }, radius, tool.value === 'eraser' ? 0 : 210)
  }
}

function fillRectangle(from: Point, to: Point, value: number) {
  if (!mask.value) return
  const { width, height } = imageSize.value
  const left = Math.max(0, Math.floor(Math.min(from.x, to.x)))
  const right = Math.min(width - 1, Math.ceil(Math.max(from.x, to.x)))
  const top = Math.max(0, Math.floor(Math.min(from.y, to.y)))
  const bottom = Math.min(height - 1, Math.ceil(Math.max(from.y, to.y)))
  for (let y = top; y <= bottom; y += 1) for (let x = left; x <= right; x += 1) mask.value[y * width + x] = value
}

function pointInPolygon(point: Point, polygon: Point[]) {
  let inside = false
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const intersects = polygon[i].y > point.y !== polygon[j].y > point.y && point.x < (polygon[j].x - polygon[i].x) * (point.y - polygon[i].y) / (polygon[j].y - polygon[i].y) + polygon[i].x
    if (intersects) inside = !inside
  }
  return inside
}

function fillLasso(points: Point, polygon: Point[]) {
  if (!mask.value || polygon.length < 3) return
  const { width, height } = imageSize.value
  const minX = Math.max(0, Math.floor(Math.min(...polygon.map((item) => item.x))))
  const maxX = Math.min(width - 1, Math.ceil(Math.max(...polygon.map((item) => item.x))))
  const minY = Math.max(0, Math.floor(Math.min(...polygon.map((item) => item.y))))
  const maxY = Math.min(height - 1, Math.ceil(Math.max(...polygon.map((item) => item.y))))
  for (let y = minY; y <= maxY; y += 1) for (let x = minX; x <= maxX; x += 1) if (pointInPolygon({ x, y }, polygon)) mask.value[y * width + x] = tool.value === 'eraser' ? 0 : 210
}

function onPointerDown(event: PointerEvent) {
  const canvas = canvasRef.value
  if (!canvas) return
  canvas.setPointerCapture(event.pointerId)
  if (event.button === 1 || spacePressed.value) {
    isPanning = true
    panStart = { x: event.clientX, y: event.clientY }
    panOrigin = { ...pan.value }
    return
  }
  if (tool.value === 'select') return
  const point = toImagePoint(getCanvasPoint(event))
  if (!point) return
  pushUndo()
  isDrawing = true
  lastPoint = point
  startPoint = point
  lassoPoints = [point]
  if (tool.value === 'brush' || tool.value === 'eraser') paintCircle(point, brushSize.value / 2, tool.value === 'eraser' ? 0 : 210)
  render()
}

function onPointerMove(event: PointerEvent) {
  if (isPanning) {
    pan.value = { x: panOrigin.x + event.clientX - panStart.x, y: panOrigin.y + event.clientY - panStart.y }
    render()
    return
  }
  if (!isDrawing) return
  const point = toImagePoint(getCanvasPoint(event))
  if (!point || !lastPoint) return
  if (tool.value === 'brush' || tool.value === 'eraser') paintLine(lastPoint, point, tool.value === 'eraser' ? 0 : 210)
  if (tool.value === 'lasso') lassoPoints.push(point)
  lastPoint = point
  render()
}

function onPointerUp(event: PointerEvent) {
  if (isPanning) {
    isPanning = false
    return
  }
  if (!isDrawing || !startPoint || !mask.value) return
  const point = toImagePoint(getCanvasPoint(event)) || lastPoint || startPoint
  if (tool.value === 'rectangle') fillRectangle(startPoint, point, 210)
  if (tool.value === 'lasso') fillLasso(point, lassoPoints)
  isDrawing = false
  lastPoint = null
  startPoint = null
  lassoPoints = []
  syncMaskCanvas()
  setStatus('Mask updated')
}

function onWheel(event: WheelEvent) {
  if (!image.value) return
  event.preventDefault()
  const before = getCanvasPoint(event)
  const oldZoom = zoom.value
  const nextZoom = Math.min(4, Math.max(0.1, oldZoom * (event.deltaY < 0 ? 1.1 : 0.9)))
  const layout = imageLayout()
  const imagePoint = { x: (before.x - layout.x) / oldZoom, y: (before.y - layout.y) / oldZoom }
  zoom.value = nextZoom
  const nextLayout = imageLayout()
  pan.value = { x: pan.value.x + before.x - (nextLayout.x + imagePoint.x * nextZoom), y: pan.value.y + before.y - (nextLayout.y + imagePoint.y * nextZoom) }
  render()
}

function clearMask() {
  if (!mask.value || !mask.value.some(Boolean)) return
  pushUndo()
  mask.value.fill(0)
  syncMaskCanvas()
  setStatus('Mask cleared')
}

function invertMask() {
  if (!mask.value) return
  pushUndo()
  for (let i = 0; i < mask.value.length; i += 1) mask.value[i] = 255 - mask.value[i]
  syncMaskCanvas()
  setStatus('Mask inverted')
}

function drawTemporary(ctx: CanvasRenderingContext2D, layout: ReturnType<typeof imageLayout>) {
  if (!isDrawing || !startPoint || !lastPoint) return
  ctx.save()
  ctx.strokeStyle = '#2563eb'
  ctx.lineWidth = 1.5
  ctx.setLineDash([6, 4])
  if (tool.value === 'rectangle') {
    const x = layout.x + Math.min(startPoint.x, lastPoint.x) * zoom.value
    const y = layout.y + Math.min(startPoint.y, lastPoint.y) * zoom.value
    const width = Math.abs(lastPoint.x - startPoint.x) * zoom.value
    const height = Math.abs(lastPoint.y - startPoint.y) * zoom.value
    ctx.strokeRect(x, y, width, height)
  }
  if (tool.value === 'lasso' && lassoPoints.length > 1) {
    ctx.beginPath()
    ctx.moveTo(layout.x + lassoPoints[0].x * zoom.value, layout.y + lassoPoints[0].y * zoom.value)
    lassoPoints.slice(1).forEach((point) => ctx.lineTo(layout.x + point.x * zoom.value, layout.y + point.y * zoom.value))
    ctx.stroke()
  }
  ctx.restore()
}

function render() {
  const canvas = canvasRef.value
  const currentImage = image.value
  if (!canvas) return
  const rect = canvas.getBoundingClientRect()
  const dpr = window.devicePixelRatio || 1
  if (canvas.width !== Math.max(1, Math.floor(rect.width * dpr)) || canvas.height !== Math.max(1, Math.floor(rect.height * dpr))) {
    canvas.width = Math.max(1, Math.floor(rect.width * dpr))
    canvas.height = Math.max(1, Math.floor(rect.height * dpr))
  }
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, rect.width, rect.height)
  if (!currentImage) return
  const layout = imageLayout()
  ctx.save()
  ctx.shadowColor = 'rgba(15, 23, 42, .16)'
  ctx.shadowBlur = 24
  ctx.shadowOffsetY = 8
  ctx.drawImage(currentImage, layout.x, layout.y, layout.width, layout.height)
  ctx.restore()
  if (maskVisible.value && maskCanvas && mask.value) {
    ctx.save()
    ctx.globalAlpha = overlayOpacity.value
    ctx.drawImage(maskCanvas, layout.x, layout.y, layout.width, layout.height)
    ctx.restore()
  }
  drawTemporary(ctx, layout)
}

function resetZoom() {
  resetViewport()
  render()
}

function downloadImage() {
  if (!image.value) return
  const exportCanvas = document.createElement('canvas')
  exportCanvas.width = image.value.naturalWidth
  exportCanvas.height = image.value.naturalHeight
  const context = exportCanvas.getContext('2d')
  if (!context) return
  context.drawImage(image.value, 0, 0)
  exportCanvas.toBlob((blob) => {
    if (!blob) return
    const anchor = document.createElement('a')
    anchor.href = URL.createObjectURL(blob)
    anchor.download = imageName.value.replace(/\.[^.]+$/, '') + '-edited.png'
    anchor.click()
    URL.revokeObjectURL(anchor.href)
    setStatus('Download started')
  }, 'image/png')
}

function blobFromMask() {
  if (!mask.value) return Promise.resolve(new Blob())
  const { width, height } = imageSize.value
  const exportCanvas = document.createElement('canvas')
  exportCanvas.width = width
  exportCanvas.height = height
  const context = exportCanvas.getContext('2d')
  if (!context) return Promise.resolve(new Blob())
  const pixels = new Uint8ClampedArray(mask.value.length * 4)
  for (let index = 0; index < mask.value.length; index += 1) {
    const value = mask.value[index] > 0 ? 255 : 0
    pixels[index * 4] = value
    pixels[index * 4 + 1] = value
    pixels[index * 4 + 2] = value
    pixels[index * 4 + 3] = 255
  }
  context.putImageData(new ImageData(pixels, width, height), 0, 0)
  return new Promise<Blob>((resolve) => exportCanvas.toBlob((blob) => resolve(blob || new Blob()), 'image/png'))
}

/**
 * Blend the generated frame with the original one through the mask.
 *
 * The API has no mask parameter, so the model may repaint the whole canvas. Everything
 * outside the selection is copied back from the source at the target size, and the
 * editable area gets a feathered edge so seams do not show.
 */
function compositeThroughMask(generated: HTMLImageElement, original: HTMLImageElement): HTMLCanvasElement {
  const width = generated.naturalWidth
  const height = generated.naturalHeight
  const output = document.createElement('canvas')
  output.width = width
  output.height = height
  const context = output.getContext('2d')
  if (!context || !mask.value) {
    context?.drawImage(generated, 0, 0, width, height)
    return output
  }

  const sourceCanvas = document.createElement('canvas')
  sourceCanvas.width = width
  sourceCanvas.height = height
  const sourceContext = sourceCanvas.getContext('2d')
  if (!sourceContext) return output
  sourceContext.drawImage(original, 0, 0, width, height)

  // Alpha map in output coordinates: opaque where the user painted, transparent elsewhere.
  const maskCanvas = document.createElement('canvas')
  maskCanvas.width = width
  maskCanvas.height = height
  const maskContext = maskCanvas.getContext('2d')
  if (!maskContext) return output
  const scaled = maskContext.createImageData(width, height)
  const sourceWidth = imageSize.value.width || width
  const sourceHeight = imageSize.value.height || height
  for (let y = 0; y < height; y += 1) {
    const sourceY = Math.min(sourceHeight - 1, Math.floor((y / height) * sourceHeight))
    for (let x = 0; x < width; x += 1) {
      const sourceX = Math.min(sourceWidth - 1, Math.floor((x / width) * sourceWidth))
      const offset = (y * width + x) * 4
      scaled.data[offset + 3] = mask.value[sourceY * sourceWidth + sourceX] > 0 ? 255 : 0
    }
  }
  maskContext.putImageData(scaled, 0, 0)

  // Keep only the generated pixels that fall under the mask, feathered by 2 px.
  const editable = document.createElement('canvas')
  editable.width = width
  editable.height = height
  const editableContext = editable.getContext('2d')
  if (!editableContext) return output
  editableContext.filter = 'blur(2px)'
  editableContext.drawImage(maskCanvas, 0, 0)
  editableContext.filter = 'none'
  editableContext.globalCompositeOperation = 'source-in'
  editableContext.drawImage(generated, 0, 0, width, height)

  context.drawImage(sourceCanvas, 0, 0)
  context.drawImage(editable, 0, 0)
  return output
}

function canvasToBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve) => canvas.toBlob((blob) => resolve(blob || new Blob()), 'image/png'))
}

async function generate() {
  if (!canGenerate.value || !image.value || !sourceBlob.value) return
  const original = image.value
  const beforeGeneration = captureSnapshot()
  isGenerating.value = true
  setStatus('Generating...')
  try {
    const form = new FormData()
    form.append('image', sourceBlob.value, imageName.value || 'image.png')
    form.append('mask', await blobFromMask(), 'mask.png')
    form.append('prompt', prompt.value.trim())
    form.append('model', model.value)
    const response = await fetch('/api/inpaint', { method: 'POST', body: form })
    if (!response.ok) {
      let message = `Generation failed (${response.status})`
      try {
        const body = await response.json() as { detail?: string; error?: string }
        message = body.detail || body.error || message
      } catch { /* non-json server error */ }
      throw new Error(message)
    }
    const resultBlob = await response.blob()
    if (!resultBlob.type.startsWith('image/')) throw new Error('Backend returned a non-image response.')

    const resultUrl = URL.createObjectURL(resultBlob)
    const generated = await new Promise<HTMLImageElement>((resolve, reject) => {
      const loader = new Image()
      loader.onload = () => resolve(loader)
      loader.onerror = () => reject(new Error('Generated image could not be decoded.'))
      loader.src = resultUrl
    })

    // Restore unmarked pixels, then hand the composited frame back as the new document.
    const composited = compositeThroughMask(generated, original)
    const finalBlob = await canvasToBlob(composited)
    const finalUrl = URL.createObjectURL(finalBlob)
    URL.revokeObjectURL(resultUrl)

    const finalImage = await new Promise<HTMLImageElement>((resolve, reject) => {
      const loader = new Image()
      loader.onload = () => resolve(loader)
      loader.onerror = () => reject(new Error('Composited image could not be decoded.'))
      loader.src = finalUrl
    })

    if (beforeGeneration) {
      undoStack.value.push(beforeGeneration)
      if (undoStack.value.length > 20) undoStack.value.shift()
      redoStack.value = []
    }
    image.value = finalImage
    imageUrl.value = finalUrl
    sourceBlob.value = finalBlob
    imageSize.value = { width: finalImage.naturalWidth, height: finalImage.naturalHeight }
    imageName.value = `${imageName.value.replace(/\.[^.]+$/, '')}-generated.png`
    makeMask(finalImage.naturalWidth, finalImage.naturalHeight)
    const meta = MODEL_LABELS[model.value]
    const item: HistoryItem = {
      id: idCounter++,
      url: finalUrl,
      label: meta ? `${meta.tier} · ${generationHistory.value.length + 1}` : `Generation ${generationHistory.value.length + 1}`,
      createdAt: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    }
    generationHistory.value.unshift(item)
    activeHistory.value = 0
    resetViewport()
    setStatus('Generation complete')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'Generation failed.'
    status.value = 'Error'
  } finally {
    isGenerating.value = false
  }
}

function activateHistory(item: HistoryItem, index: number) {
  const selected = new Image()
  selected.onload = () => {
    image.value = selected
    imageUrl.value = item.url
    sourceBlob.value = null
    imageSize.value = { width: selected.naturalWidth, height: selected.naturalHeight }
    makeMask(selected.naturalWidth, selected.naturalHeight)
    activeHistory.value = index
    resetViewport()
  }
  selected.src = item.url
}

function handleKey(event: KeyboardEvent) {
  const mod = event.metaKey || event.ctrlKey
  if (mod && event.key.toLowerCase() === 'z') { event.preventDefault(); event.shiftKey ? redo() : undo() }
  if (mod && event.key.toLowerCase() === 'y') { event.preventDefault(); redo() }
  if (event.code === 'Space') {
    const target = event.target as HTMLElement | null
    const isTextEntry = target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA' || target?.tagName === 'SELECT' || target?.isContentEditable
    if (!isTextEntry) {
      event.preventDefault()
      spacePressed.value = event.type === 'keydown'
    }
  }
}

function handleResize() { render() }

onMounted(() => {
  loadSettings()
  loadCatalog()
  window.addEventListener('keydown', handleKey)
  window.addEventListener('keyup', handleKey)
  window.addEventListener('resize', handleResize)
  if (workspaceRef.value) {
    resizeObserver = new ResizeObserver(handleResize)
    resizeObserver.observe(workspaceRef.value)
  }
  nextTick(render)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKey)
  window.removeEventListener('keyup', handleKey)
  window.removeEventListener('resize', handleResize)
  resizeObserver?.disconnect()
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
})
</script>

<template>
  <main class="editor-shell">
    <header class="topbar">
      <div class="brand-lockup">
        <div class="brand-mark"><Sparkles :size="16" /></div>
        <div><strong>Image Editor</strong><span>AI native</span></div>
      </div>
      <div class="topbar-actions">
        <button class="icon-button" title="Undo" :disabled="!undoStack.length" @click="undo"><Undo2 :size="17" /></button>
        <button class="icon-button" title="Redo" :disabled="!redoStack.length" @click="redo"><Redo2 :size="17" /></button>
        <span class="divider" />
        <button class="secondary-button" :disabled="!image" @click="downloadImage"><Download :size="16" /> Download</button>
        <button class="primary-button" :disabled="!canGenerate" @click="generate">
          <LoaderCircle v-if="isGenerating" class="spin" :size="16" />
          <WandSparkles v-else :size="16" />
          {{ isGenerating ? 'Generating' : 'Generate' }}
        </button>
      </div>
    </header>

    <section class="editor-layout">
      <div
        ref="workspaceRef"
        class="workspace"
        :class="{ 'is-dragging': isDraggingFile, 'has-image': image }"
        @dragover.prevent="isDraggingFile = true"
        @dragleave.prevent="isDraggingFile = false"
        @drop="onDrop"
      >
        <div class="workspace-meta">
          <span v-if="image"><FileImage :size="14" /> {{ imageName || 'Untitled image' }}</span>
          <span v-else>Canvas workspace</span>
          <span v-if="image" class="resolution">{{ imageSize.width }} x {{ imageSize.height }} px</span>
        </div>
        <canvas
          ref="canvasRef"
          class="editor-canvas"
          @pointerdown="onPointerDown"
          @pointermove="onPointerMove"
          @pointerup="onPointerUp"
          @pointercancel="onPointerUp"
          @wheel="onWheel"
        />
        <div v-if="showWelcome" class="empty-state">
          <div class="empty-icon"><ImagePlus :size="22" /></div>
          <h1>Start with an image</h1>
          <p>Drop a PNG, JPG, or WebP here to begin editing</p>
          <button class="primary-button" @click="fileInputRef?.click()"><Upload :size="16" /> Upload image</button>
          <span>or paste from clipboard</span>
        </div>
        <div class="floating-toolbar" aria-label="Canvas tools">
          <button
            v-for="item in toolItems"
            :key="item.id"
            class="tool-button"
            :class="{ active: selectedTool === item.id }"
            :title="item.label"
            @click="tool = item.id"
          ><component :is="item.icon" :size="17" /></button>
          <span class="toolbar-divider" />
          <button class="tool-button" title="Zoom out" @click="zoom = Math.max(.1, zoom - .1); render()"><Minus :size="16" /></button>
          <span class="zoom-label">{{ Math.round(zoom * 100) }}%</span>
          <button class="tool-button" title="Zoom in" @click="zoom = Math.min(4, zoom + .1); render()"><Plus :size="16" /></button>
          <button class="tool-button" title="Reset view" @click="resetZoom"><RotateCcw :size="16" /></button>
        </div>
        <div class="canvas-hint"><Hand :size="13" /> Space + drag to pan <span>•</span> Wheel to zoom</div>
      </div>

      <aside class="side-panel">
        <div class="panel-scroll">
          <section class="panel-section upload-section">
            <div class="section-heading"><span>Source image</span><span v-if="image" class="status-dot"><Check :size="12" /></span></div>
            <button class="upload-control" @click="fileInputRef?.click">
              <Upload :size="16" /><span>{{ image ? 'Replace image' : 'Upload image' }}</span><kbd>⌘O</kbd>
            </button>
            <input ref="fileInputRef" class="visually-hidden" type="file" accept="image/png,image/jpeg,image/webp" @change="onFileInput" />
          </section>

          <section class="panel-section">
            <div class="section-heading"><span>Mask</span><button class="text-button" :disabled="!mask" @click="clearMask"><Trash2 :size="13" /> Clear</button></div>
            <div class="mask-tools">
              <button class="mask-action" :disabled="!mask" @click="invertMask"><Blend :size="15" /> Invert</button>
              <button class="mask-action" :disabled="!mask" @click="maskVisible = !maskVisible"><component :is="maskVisible ? Eye : EyeOff" :size="15" /> {{ maskVisible ? 'Hide' : 'Show' }}</button>
            </div>
            <label class="field-label">Brush size <output>{{ brushSize }} px</output></label>
            <input v-model.number="brushSize" class="range-input" type="range" min="1" max="100" />
            <label class="field-label">Overlay opacity <output>{{ Math.round(overlayOpacity * 100) }}%</output></label>
            <input v-model.number="overlayOpacity" class="range-input" type="range" min="0.1" max="0.8" step="0.01" @input="render" />
          </section>

          <section class="panel-section model-section">
            <div class="section-heading">
              <span><Cpu :size="14" /> Model</span>
              <button v-if="catalogState === 'error'" class="text-button" @click="loadCatalog"><RotateCcw :size="13" /> Retry</button>
            </div>
            <p v-if="catalogState === 'loading'" class="model-hint"><LoaderCircle class="spin" :size="14" /> Loading models...</p>
            <p v-else-if="catalogState === 'error'" class="model-hint error">{{ catalogError }}</p>
            <div v-else class="model-groups">
              <div v-for="group in modelGroups" :key="group.key" class="model-group">
                <span class="model-group-name">{{ group.provider }} {{ group.family }}</span>
                <div class="model-tiers">
                  <button
                    v-for="entry in group.tiers"
                    :key="entry.id"
                    class="model-card"
                    :class="{ active: model === entry.id }"
                    @click="selectModel(entry.id)"
                  >
                    <span class="model-tier">{{ modelMeta(entry).tier }}</span>
                    <span class="model-note">{{ modelMeta(entry).note }}</span>
                  </button>
                </div>
              </div>
            </div>
            <p class="model-current"><Check :size="12" /> {{ activeModel ? modelMeta(activeModel).provider + ' ' + modelMeta(activeModel).family + ' ' + modelMeta(activeModel).tier : model }}</p>
          </section>

          <section class="panel-section prompt-section">
            <div class="section-heading"><span>Prompt</span><span class="required-label">Required</span></div>
            <textarea v-model="prompt" rows="4" placeholder="Describe the change you want to make..." />
            <div class="prompt-footer"><span>{{ prompt.length }} / 500</span><span>Be specific</span></div>
          </section>

          <section v-if="generationHistory.length" class="panel-section history-section">
            <div class="section-heading"><span>Generation history</span><span class="history-count">{{ generationHistory.length }}</span></div>
            <div class="history-grid">
              <button v-for="(item, index) in generationHistory" :key="item.id" class="history-item" :class="{ active: activeHistory === index }" @click="activateHistory(item, index)"><img :src="item.url" :alt="item.label" /><span>{{ item.label }}<small>{{ item.createdAt }}</small></span></button>
            </div>
          </section>
        </div>
        <div class="panel-status" :class="{ error: errorMessage }"><span><span class="status-indicator" :class="{ busy: isGenerating, error: errorMessage }" />{{ errorMessage || status }}</span><button v-if="errorMessage" class="close-error" title="Dismiss" @click="errorMessage = ''"><X :size="14" /></button></div>
      </aside>
    </section>
  </main>
</template>
