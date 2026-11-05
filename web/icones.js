// Ícones do painel em pixel art, desenhados para este projeto (sem dependências)
(function () {
  // Cada letra é uma cor da paleta; "." é transparente. Pixels vizinhos da mesma cor viram um retângulo só.
  function pixel(linhas, paleta) {
    let rects = "";
    linhas.forEach((linha, y) => {
      for (let x = 0; x < linha.length; ) {
        const c = linha[x];
        let fim = x + 1;
        while (fim < linha.length && linha[fim] === c) fim++;
        if (c !== ".") rects += `<rect x="${x}" y="${y}" width="${fim - x}" height="1" fill="${paleta[c]}"/>`;
        x = fim;
      }
    });
    const largura = linhas[0].length;
    return `<svg viewBox="0 0 ${largura} ${linhas.length}" width="1em" height="1em" shape-rendering="crispEdges" aria-hidden="true">${rects}</svg>`;
  }

  const ATUAL = { c: "currentColor" };

  const relogio = pixel([
    "....kkkk....",
    "..kkyyyykk..",
    ".kyyyykyyyk.",
    ".kyyyykyyyk.",
    "kyyyyykyyyyk",
    "kyyyyykyyyyk",
    "kyyyyykkkyyk",
    "kyyyyyyyyyyk",
    ".kyyyyyyyyk.",
    ".kyyyyyyyyk.",
    "..kkyyyykk..",
    "....kkkk....",
  ], { k: "#2e2200", y: "#f4c430" });

  const aviso = pixel([
    ".....kk.....",
    "....kyyk....",
    "....kyyk....",
    "...kyyyyk...",
    "...kykkyk...",
    "..kyykkyyk..",
    "..kyykkyyk..",
    ".kyyykkyyyk.",
    ".kyyyyyyyyk.",
    "kyyyykkyyyyk",
    "kyyyyyyyyyyk",
    "kkkkkkkkkkkk",
  ], { k: "#2e2200", y: "#ffc21a" });

  const ICONES = {
    relogio,
    despertador: relogio,
    aviso,
    alerta: aviso,
    local: pixel([
      "...krrrrk...",
      "..krrrrrrk..",
      ".krrwwwwrrk.",
      ".krrwwwwrrk.",
      ".krrwwwwrrk.",
      ".krrrrrrrrk.",
      "..krrrrrrk..",
      "..krrrrrrk..",
      "...krrrrk...",
      "....krrk....",
      ".....kk.....",
      ".....kk.....",
    ], { k: "#3a0606", r: "#e53935", w: "#fff0c0" }),
    sensor: pixel([
      "kkkkkkkkkkkk",
      "kssssssssssk",
      "ksddssssddsk",
      "ksdrdssdrdsk",
      "ksddssssddsk",
      "kssssssssssk",
      "kssdddddd ssk".replace(" ", "s").slice(0, 12),
      "kssdssssdssk",
      "kssssssssssk",
      "kkkkkkkkkkkk",
      "....kddk....",
      "....kddk....",
    ], { k: "#111111", s: "#8f8f8f", d: "#3c3c3c", r: "#ff4d4d" }),
    estaca: pixel([
      "....gg......",
      "...gGGg.gg..",
      "...gGGggGGg.",
      "....gGGGGg..",
      "..gg.gGGg...",
      ".gGGggGg....",
      ".gGGGGg.....",
      "..ggGGb.....",
      ".....bb.....",
      ".....bb.....",
      "..ddddddd...",
      "..ddddddd...",
    ], { g: "#2f7d22", G: "#6fd14a", b: "#7a5230", d: "#5c3b20" }),
    trigo: pixel([
      "...y..y..y..",
      "..yy.yy.yy..",
      "...yy.yy.y..",
      "..yy.yy.yy..",
      "...y..yy.y..",
      "...g..g..g..",
      "...g..g..g..",
      "...g..g..g..",
      "BBBBBBBBBBBB",
      "dBdddBdddBdd",
      "ddBdddBdddBd",
      "dddddddddddd",
    ], { y: "#ffd24a", g: "#5fbf3a", B: "#3e2610", d: "#5c3b20" }),
    seco: pixel([
      "....d.......",
      "...dd.d.....",
      "....ddd.....",
      ".....d......",
      ".....d......",
      "SSSSSSSSSSSS",
      "SsSSSkSSsSSS",
      "SSSSkSSSSSkS",
      "SSkSkSSSkkSS",
      "SSSkSSsSkSSS",
      "sSSSSSSkSSSS",
      "SSSsSSSSSSsS",
    ], { d: "#9c7a3c", S: "#b8926a", s: "#d0ac84", k: "#5c3b20" }),
    gota: pixel([
      ".....kk.....",
      "....kwwk....",
      "....kwwk....",
      "...kwwwwk...",
      "..kwwwwwwk..",
      ".kwLwwwwwwk.",
      ".kwLwwwwwwk.",
      ".kwLLwwwwwk.",
      ".kwwLwwwwwk.",
      "..kwwwwwwk..",
      "...kkwwkk...",
      ".....kk.....",
    ], { k: "#0d2f66", w: "#3f8ee8", L: "#c8eaff" }),
    check: pixel([
      "..........kk",
      ".........kgk",
      "........kggk",
      ".......kggk.",
      "kk....kggk..",
      "kgk..kggk...",
      "kggkkggk....",
      ".kgggggk....",
      "..kgggk.....",
      "...kgk......",
      "....k.......",
      "............",
    ], { k: "#0c3a10", g: "#55ff55" }),
    desligado: pixel([
      "rr........rr",
      "rrr......rrr",
      ".rrr....rrr.",
      "..rrr..rrr..",
      "...rrrrrr...",
      "....rrrr....",
      "....rrrr....",
      "...rrrrrr...",
      "..rrr..rrr..",
      ".rrr....rrr.",
      "rrr......rrr",
      "rr........rr",
    ], { r: "#ff5a5a" }),
    lixeira: pixel([
      "....cccc....",
      "cccccccccccc",
      "............",
      ".cccccccccc.",
      ".c.cc.cc.cc.".slice(0, 12),
      ".c.cc.cc.cc.",
      ".c.cc.cc.cc.",
      ".c.cc.cc.cc.",
      ".c.cc.cc.cc.",
      ".c.cc.cc.cc.",
      ".cccccccccc.",
      "............",
    ], ATUAL),
    lapis: pixel([
      "..........rr",
      ".........rrr",
      "........yyr.",
      ".......yyy..",
      "......yyy...",
      ".....yyy....",
      "....yyy.....",
      "...yyy......",
      "..yyy.......",
      ".wwy........",
      ".kw.........",
      "k...........",
    ], { r: "#e57373", y: "#ffc21a", w: "#f2dcb8", k: "#222222" }),
    mais: pixel([
      "....cccc....",
      "....cccc....",
      "....cccc....",
      "....cccc....",
      "cccccccccccc",
      "cccccccccccc",
      "cccccccccccc",
      "cccccccccccc",
      "....cccc....",
      "....cccc....",
      "....cccc....",
      "....cccc....",
    ], ATUAL),
    play: pixel([
      "..cc........",
      "..cccc......",
      "..cccccc....",
      "..cccccccc..",
      "..cccccccccc",
      "..cccccccccc",
      "..cccccccccc",
      "..cccccccccc",
      "..cccccccc..",
      "..cccccc....",
      "..cccc......",
      "..cc........",
    ], ATUAL),
    stop: pixel([
      "............",
      ".cccccccccc.",
      ".cccccccccc.",
      ".cccccccccc.",
      ".cccccccccc.",
      ".cccccccccc.",
      ".cccccccccc.",
      ".cccccccccc.",
      ".cccccccccc.",
      ".cccccccccc.",
      ".cccccccccc.",
      "............",
    ], ATUAL),
    // bloco de comando: a linguagem MineC roda aqui
    codigo: pixel([
      "kkkkkkkkkkkk",
      "kOOOOOOOOOOk",
      "kOwOOOOOOOOk",
      "kOwwOOOOOOOk",
      "kOOwwOOOOOOk",
      "kOOOwwOOOOOk",
      "kOOwwOOOOOOk",
      "kOwwOOOOOOOk",
      "kOwOOOwwwwOk",
      "kOOOOOwwwwOk",
      "kOOOOOOOOOOk",
      "kkkkkkkkkkkk",
    ], { k: "#3a1d05", O: "#d9782b", w: "#fff4e0" }),
  };

  // Bloco de terra com grama (16 x 16), a marca do painel
  function blocoPixel() {
    const svg = pixel([
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
    ], { g: "#5fa83a", G: "#7cc34d", h: "#3f7d26", d: "#8a5a32", D: "#6b4423", e: "#a87443" });
    return svg.replace('width="1em" height="1em"', 'width="100%" height="100%"');
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
