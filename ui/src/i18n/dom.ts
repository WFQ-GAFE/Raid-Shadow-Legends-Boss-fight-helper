import { backendText } from './index'
import type { Locale } from './locales'

// A safety net under the components: text in the page that came from the
// backend (message tokens) or from records saved before 1.1.2 (Chinese) is
// shown in the window's language. data-i18n-skip marks text left as it is.
const trackedText = new WeakMap<Text, { source: string; rendered: string }>()
const trackedAttributes = new WeakMap<Element, Map<string, { source: string; rendered: string }>>()
const translatedAttributes = ['placeholder', 'title', 'aria-label'] as const

function isSkipped(node: Node): boolean {
  const element = node.nodeType === Node.ELEMENT_NODE
    ? node as Element
    : node.parentElement
  return Boolean(element?.closest('[data-i18n-skip]'))
}

function localizeTextNode(node: Text, language: Locale) {
  if (isSkipped(node)) return
  const current = node.nodeValue ?? ''
  let tracked = trackedText.get(node)
  if (!tracked || current !== tracked.rendered) {
    tracked = { source: current, rendered: current }
  }
  const rendered = backendText(tracked.source, language)
  tracked.rendered = rendered
  trackedText.set(node, tracked)
  if (current !== rendered) node.nodeValue = rendered
}

function localizeElementAttributes(element: Element, language: Locale) {
  if (isSkipped(element)) return
  const tracked = trackedAttributes.get(element) ?? new Map()
  for (const attribute of translatedAttributes) {
    const current = element.getAttribute(attribute)
    if (current === null) continue
    let value = tracked.get(attribute)
    if (!value || current !== value.rendered) {
      value = { source: current, rendered: current }
    }
    const rendered = backendText(value.source, language)
    value.rendered = rendered
    tracked.set(attribute, value)
    if (current !== rendered) element.setAttribute(attribute, rendered)
  }
  trackedAttributes.set(element, tracked)
}

function localizeSubtree(root: Node, language: Locale) {
  if (root.nodeType === Node.TEXT_NODE) localizeTextNode(root as Text, language)
  if (root.nodeType === Node.ELEMENT_NODE) localizeElementAttributes(root as Element, language)
  const walker = document.createTreeWalker(
    root,
    NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT,
  )
  let node = walker.nextNode()
  while (node) {
    if (node.nodeType === Node.TEXT_NODE) localizeTextNode(node as Text, language)
    else localizeElementAttributes(node as Element, language)
    node = walker.nextNode()
  }
}

export function installDocumentLocalization(root: HTMLElement, language: Locale) {
  document.documentElement.lang = language
  localizeSubtree(root, language)
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'characterData') {
        localizeTextNode(mutation.target as Text, language)
        continue
      }
      if (mutation.type === 'attributes') {
        localizeElementAttributes(mutation.target as Element, language)
        continue
      }
      for (const node of mutation.addedNodes) localizeSubtree(node, language)
    }
  })
  observer.observe(root, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: [...translatedAttributes],
  })
  return () => observer.disconnect()
}
