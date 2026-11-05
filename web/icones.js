// Ícones do painel (SVG próprios, sem dependências)
(function () {
  const traco = (corpo, extra = "") =>
    `<svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${corpo}</svg>`;

  const ICONES = {
    relogio: traco('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    local: `<svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true"><path fill="#ef4444" d="M12 2a7 7 0 0 0-7 7c0 5 7 13 7 13s7-8 7-13a7 7 0 0 0-7-7z"/><circle cx="12" cy="9" r="2.6" fill="#fde68a"/></svg>`,
    sensor: `<svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true"><rect x="7" y="2" width="10" height="7" rx="1.5" fill="#c4b5fd"/><path d="M9 9v11l1.5 2L12 20V9M12 9v11l1.5 2L15 20V9" fill="#a78bfa"/><circle cx="12" cy="5.5" r="1.4" fill="#4c1d95"/></svg>`,
    alerta: `<svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true"><path d="M12 3 2 21h20z" fill="#f59e0b"/><path d="M12 10v5" stroke="#3b2405" stroke-width="2.2" stroke-linecap="round"/><circle cx="12" cy="18" r="1.2" fill="#3b2405"/></svg>`,
    despertador: `<svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true"><circle cx="12" cy="13" r="8" fill="#c4b5fd"/><circle cx="12" cy="13" r="5.6" fill="#ede9fe"/><path d="M12 10v3.2l2 1.4" stroke="#4c1d95" stroke-width="1.8" stroke-linecap="round" fill="none"/><path d="M4 6l3-3M20 6l-3-3" stroke="#a78bfa" stroke-width="2" stroke-linecap="round"/></svg>`,
    estaca: `<svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true"><path d="M12 13c0-4 3-7 7-7 0 4-3 7-7 7z" fill="#86efac"/><path d="M12 15c0-3-2.5-5.5-6-5.5 0 3 2.5 5.5 6 5.5z" fill="#4ade80"/><path d="M12 12v9" stroke="#fde68a" stroke-width="2" stroke-linecap="round"/><rect x="7" y="19" width="10" height="3" rx="1" fill="#fbbf24"/></svg>`,
    lixeira: traco('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/>'),
    gota: `<svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true"><path fill="currentColor" d="M12 2.5S5 10.3 5 14.6A7 7 0 0 0 19 14.6C19 10.3 12 2.5 12 2.5z"/></svg>`,
    check: traco('<path d="M5 12.5l4.5 4.5L19 7.5"/>', 'stroke-width="3"'),
    aviso: `<svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true"><path d="M12 3 2 21h20z" fill="#f59e0b"/><path d="M12 10v5" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/><circle cx="12" cy="18" r="1.2" fill="#fff"/></svg>`,
    desligado: traco('<path d="M2 8.5a15 15 0 0 1 5-3M10.5 4.6A15 15 0 0 1 22 8.5M5 12a10 10 0 0 1 4-2.4M15.5 9.9A10 10 0 0 1 19 12M8.5 15.5a5 5 0 0 1 7 0M3 3l18 18"/><circle cx="12" cy="19" r="1" fill="currentColor"/>'),
    lapis: traco('<path d="M4 20h4L19 9l-4-4L4 16z M14 6l4 4"/>'),
    mais: traco('<path d="M12 5v14M5 12h14"/>', 'stroke-width="2.6"'),
    play: `<svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true"><path fill="currentColor" d="M7 4.5v15l12.5-7.5z"/></svg>`,
    stop: `<svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true"><rect fill="currentColor" x="6" y="6" width="12" height="12" rx="2"/></svg>`,
    codigo: traco('<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"/>'),
  };

  // Bloco de terra com grama em pixel art (16 x 16)
  function blocoPixel() {
    const paleta = { g: "#5fa83a", G: "#7cc34d", h: "#3f7d26", d: "#8a5a32", D: "#6b4423", e: "#a87443" };
    const linhas = [
      "GGgGGGgGGGGgGGgG",
      "GgGGhGGGgGGGGhGG",
      "gGGGGGgGGhGgGGGg",
      "GhgGgGGhGGGGgGhG",
      "hdGhdGhdhGhdGhdh",
      "dddhdddddhddhddd",
      "ddeddDdddddeddDd",
      "dDdddddeddDdddde",
      "ddddeddddddddDdd",
      "deddddDdeddddddd",
      "dddDdddddddeddDd",
      "dddddeddDdddddde",
      "dDdddddddddDdddd",
      "ddddDdedddddddDd",
      "deddddddddeddddd",
      "DdddddDddddddDdD",
    ];
    let rects = "";
    linhas.forEach((linha, y) => {
      [...linha].forEach((c, x) => {
        rects += `<rect x="${x}" y="${y}" width="1.02" height="1.02" fill="${paleta[c]}"/>`;
      });
    });
    return `<svg viewBox="0 0 16 16" width="100%" height="100%" shape-rendering="crispEdges" aria-hidden="true">${rects}</svg>`;
  }

  window.ICONES = ICONES;
  window.icone = (nome) => ICONES[nome] || "";
  window.preencherIcones = (raiz = document) => {
    raiz.querySelectorAll("[data-icone]").forEach((el) => {
      if (!el.dataset.iconePronto) {
        el.innerHTML = ICONES[el.dataset.icone] || "";
        el.dataset.iconePronto = "1";
        el.classList.add("icone");
      }
    });
  };
  window.blocoPixel = blocoPixel;
})();
