// Mermaid diagrams at their natural size, shrunk only when wider than the column.
// sphinxcontrib-mermaid stretches every diagram to the column (max-width: 100% !important), which
// blows small ones up; an inline !important max-width (the diagram's own width) wins over that.
// The fullscreen view keeps its stretch.
;(function () {
  function fit(svg) {
    if (svg.closest('.mermaid-fullscreen-modal')) return
    var box = svg.viewBox && svg.viewBox.baseVal
    if (box && box.width && !svg.style.getPropertyPriority('max-width')) {
      svg.style.setProperty('max-width', box.width + 'px', 'important')
    }
  }
  new MutationObserver(function () {
    document.querySelectorAll('pre.mermaid > svg').forEach(fit)
  }).observe(document.documentElement, { childList: true, subtree: true })
})()
