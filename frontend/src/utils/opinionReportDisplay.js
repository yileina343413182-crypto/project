const DISPLAY_FIELDS = ['point', 'suggestion', 'risk', 'insight', 'content', 'text']
const LEADING_DOC_ID = /^\s*\[\s*doc_id\s*:\s*[^\]]+\]\s*/i

function cleanDisplayText(value) {
  return String(value ?? '').replace(LEADING_DOC_ID, '').trim()
}

export function opinionItemText(value) {
  let item = value

  if (typeof item === 'string') {
    const text = item.trim()
    if (!text.startsWith('{')) return cleanDisplayText(text)
    try {
      item = JSON.parse(text)
    } catch {
      return cleanDisplayText(text)
    }
  }

  if (!item || typeof item !== 'object' || Array.isArray(item)) return String(item ?? '')
  if (item.topic && item.insight) return cleanDisplayText(`${item.topic}：${item.insight}`)

  for (const field of DISPLAY_FIELDS) {
    if (typeof item[field] === 'string' && item[field].trim()) return cleanDisplayText(item[field])
  }
  return ''
}
