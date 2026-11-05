// MineC Irrigação: lógica do painel
(() => {
  "use strict";

  const PINOS_SENSOR = [34, 35, 32, 33, 36, 39];
  const PINOS_BOMBA = [26, 27, 25, 33, 32, 4, 5, 13, 14, 16, 17, 18, 19, 21, 22, 23];
  const INTERVALOS = [
    [30, "30 s"], [60, "1 min"], [120, "2 min"], [300, "5 min"],
    [600, "10 min"], [900, "15 min"], [1800, "30 min"], [3600, "1 h"],
  ];

  const $ = (sel, raiz = document) => raiz.querySelector(sel);
  const $$ = (sel, raiz = document) => [...raiz.querySelectorAll(sel)];

  const estado = {
    atual: null,
    seq: 0,
    console: [],
    aba: "console",
    editorSujo: false,
    compilacao: null,
    assinaturaPrograma: "",
    editandoId: null,
    arrastando: new Map(), // id -> valor local do slider de simulação
    servidorOnline: true,
    sessao: null,
    ultimoContato: 0,
  };

  // ------------------------------------------------------------------
  // utilidades
  // ------------------------------------------------------------------

  async function api(metodo, url, corpo) {
    const opcoes = { method: metodo, headers: {} };
    if (corpo !== undefined) {
      opcoes.headers["Content-Type"] = "application/json";
      opcoes.body = JSON.stringify(corpo);
    }
    const resp = await fetch(url, opcoes).catch(() => { throw new Error("Sem contato com o servidor."); });
    let dados = null;
    try { dados = await resp.json(); } catch { /* sem corpo */ }
    if (!resp.ok && resp.status !== 422) {
      throw new Error((dados && dados.erro) || `Erro ${resp.status}`);
    }
    return { status: resp.status, dados };
  }

  const dois = (n) => String(n).padStart(2, "0");
  const hora = (t, comSegundos = false) => {
    if (!t) return "--:--";
    const d = new Date(t * 1000);
    return `${dois(d.getHours())}:${dois(d.getMinutes())}${comSegundos ? ":" + dois(d.getSeconds()) : ""}`;
  };

  function textoIntervalo(s) {
    if (s < 60) return `${s} s`;
    if (s % 3600 === 0) return `${s / 3600} h`;
    if (s % 60 === 0) return `${s / 60} min`;
    return `${Math.floor(s / 60)} min ${s % 60} s`;
  }

  function contagem(fim, agora) {
    const falta = Math.max(0, Math.round(fim - agora));
    if (falta <= 1) return "agora";
    if (falta < 60) return `em ${falta} s`;
    return `em ${Math.ceil(falta / 60)} min`;
  }

  let timerToast;
  function toast(msg, erro = false) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.toggle("erro", erro);
    // com o topo na tela, fica logo abaixo do resumo para não cobrir os botões de lá
    const fimResumo = $(".resumo").getBoundingClientRect().bottom;
    el.style.top = fimResumo > 0 ? `${fimResumo + 6}px` : "";
    clearTimeout(timerToast);
    timerToast = setTimeout(() => (el.textContent = ""), 3200);
  }

  function opcoesPinos(select, pinos, atual) {
    const lista = pinos.includes(atual) || atual == null ? pinos : [atual, ...pinos];
    select.innerHTML = lista.map((p) => `<option value="${p}">GPIO ${p}</option>`).join("");
    if (atual != null) select.value = String(atual);
  }

  // ------------------------------------------------------------------
  // cartões dos canteiros
  // ------------------------------------------------------------------

  const MODELO_CARD = `
    <header class="card-topo">
      <span class="card-icone" data-icone="estaca"></span>
      <button class="card-nome" type="button" data-acao="editar">
        <h3 data-campo="nome"></h3>
        <p class="pixel-pequeno" data-campo="sensor"></p>
      </button>
      <span class="chip" data-campo="chip"></span>
      <button class="botao-icone" type="button" data-acao="remover"><span data-icone="lixeira"></span></button>
    </header>
    <div class="card-corpo">
      <div class="caixa-umidade">
        <div>
          <span class="rotulo">Umidade atual</span>
          <div class="numero"><b data-campo="umidade">--</b><small>%</small></div>
          <p class="minimo">Mínimo recomendado: <strong data-campo="minimo"></strong></p>
        </div>
        <div class="selo"><span class="icone" data-campo="selo-icone"></span><span data-campo="selo"></span></div>
      </div>
      <div>
        <div class="barra" role="meter" aria-valuemin="0" aria-valuemax="100">
          <div class="barra-preenchida" data-campo="barra"></div>
          <span class="barra-marca" data-campo="marca"></span>
        </div>
        <div class="barra-legenda"><span>0%</span><strong data-campo="legenda"></strong><span>100%</span></div>
      </div>
      <div class="infos">
        <div class="info"><span data-icone="relogio"></span><div><span>Última leitura</span><strong data-campo="ultima"></strong></div></div>
        <div class="info"><span data-icone="relogio"></span><div><span>Próx. verificação</span><strong data-campo="proxima"></strong></div></div>
      </div>
      <div class="alerta" data-campo="alerta">
        <span class="icone-alerta" data-campo="alerta-icone"></span>
        <div><strong data-campo="alerta-titulo"></strong><p data-campo="alerta-texto"></p></div>
      </div>
      <label class="simulador" data-campo="simulador" hidden>
        Simular umidade
        <input type="range" min="0" max="100" step="1">
        <output aria-live="off"></output>
      </label>
    </div>`;

  function montarCard(c) {
    const el = document.createElement("article");
    el.className = "card";
    el.dataset.id = c.id;
    el.innerHTML = MODELO_CARD;
    preencherIcones(el);

    const slider = $(".simulador input", el);
    const saida = $(".simulador output", el);
    slider.addEventListener("input", () => {
      estado.arrastando.set(c.id, Number(slider.value));
      saida.textContent = `${slider.value}%`;
      slider.style.setProperty("--pct", `${slider.value}%`);
    });
    slider.addEventListener("change", async () => {
      const canteiro = estado.atual.canteiros.find((x) => x.id === c.id);
      try {
        await api("POST", "/api/simulacao", { pino: canteiro.pino_sensor, umidade: Number(slider.value) });
      } catch (e) { toast(e.message, true); }
      setTimeout(() => estado.arrastando.delete(c.id), 1200);
    });
    return el;
  }

  function campo(el, nome) { return el.querySelector(`[data-campo="${nome}"]`); }

  function situacaoCanteiro(c) {
    const temDado = c.umidade != null && (c.situacao === "ok" || c.situacao === "simulado");
    return { temDado, seco: temDado && c.umidade < c.minimo };
  }

  function atualizarCard(el, c, indice, geral) {
    const local = estado.arrastando.get(c.id);
    const umidade = local != null ? local : c.umidade;
    // sem servidor, o último estado conhecido não vale como atual
    const online = estado.servidorOnline;
    const temLeitura = situacaoCanteiro(c).temDado;
    const temDado = online && temLeitura;
    const irrigando = online && !!c.irrigando;
    const seco = temDado && umidade < c.minimo;

    el.classList.toggle("ok", temDado && !seco);
    el.classList.toggle("offline", !temDado);
    el.classList.toggle("irrigando", irrigando);

    campo(el, "nome").textContent = c.nome;
    campo(el, "sensor").textContent = `Sensor ${dois(indice + 1)} · GPIO ${c.pino_sensor}`;
    $('[data-acao="editar"]', el).setAttribute("aria-label", `Editar ${c.nome}`);
    $('[data-acao="remover"]', el).setAttribute("aria-label", `Remover ${c.nome}`);

    const chips = { ok: "Conectado", simulado: "Simulado", sem_sinal: "Sem sinal", sem_esp32: "Offline", aguardando: "Aguardando" };
    campo(el, "chip").textContent = online ? chips[c.situacao] || c.situacao : "Sem dados";

    campo(el, "umidade").textContent = temDado ? umidade : "--";
    campo(el, "minimo").textContent = `${c.minimo}%`;
    campo(el, "legenda").textContent = `Min: ${c.minimo}%`;
    campo(el, "barra").style.width = `${temDado ? umidade : 0}%`;
    campo(el, "marca").style.left = `calc(${c.minimo}% - 1px)`;
    $(".barra", el).setAttribute("aria-valuenow", temDado ? umidade : 0);
    $(".barra", el).setAttribute("aria-label", `Umidade ${temDado ? umidade + "%" : "sem leitura"}, mínimo ${c.minimo}%`);

    let selo = "--", seloIcone = "desligado";
    if (irrigando) { selo = "REGANDO"; seloIcone = "gota"; }
    else if (temDado) { selo = seco ? "SECO" : "OK"; seloIcone = seco ? "seco" : "trigo"; }
    campo(el, "selo").textContent = selo;
    const iconeSelo = campo(el, "selo-icone");
    if (iconeSelo.dataset.atual !== seloIcone) { iconeSelo.innerHTML = icone(seloIcone); iconeSelo.dataset.atual = seloIcone; }

    campo(el, "ultima").textContent = c.ultima_verificacao ? hora(c.ultima_verificacao) : "Ainda não";
    const prog = geral.programa;
    let proxima = online ? "Parado" : "--";
    if (online && prog.rodando) {
      if (!c.usado_pelo_programa) proxima = "Fora do código";
      else if (irrigando) proxima = "Após a rega";
      else if (prog.proxima_verificacao) proxima = contagem(prog.proxima_verificacao, geral.agora);
      else proxima = "Agora";
    }
    campo(el, "proxima").textContent = proxima;

    // alerta
    const alerta = campo(el, "alerta");
    let tipo, titulo, texto, ic;
    if (!online) {
      tipo = "neutro"; ic = "desligado"; titulo = "Sem contato com o servidor";
      texto = `${temLeitura ? `Última umidade conhecida: ${c.umidade}%. ` : ""}Não dá para saber agora se a bomba está ligada.`;
    } else if (irrigando) {
      tipo = "agua"; ic = "gota"; titulo = "Irrigando agora";
      texto = `Bomba ligada no GPIO ${c.pino_bomba}. A umidade deve subir.`;
    } else if (!temDado) {
      tipo = "neutro"; ic = "desligado"; titulo = "Sem leitura";
      texto = {
        sem_esp32: "O ESP32 não está conectado ao servidor.",
        sem_sinal: "O ESP32 responde, mas o sensor parece desconectado. Confira os fios.",
        aguardando: "Esperando o ESP32 enviar a primeira leitura.",
      }[c.situacao] || "Sem dados do sensor.";
    } else if (seco) {
      tipo = ""; ic = "aviso"; titulo = "Precisa irrigar";
      texto = prog.rodando ? "Solo seco, abaixo do mínimo recomendado." : "Solo seco, mas o programa está parado.";
    } else {
      tipo = "ok"; ic = "check"; titulo = "Não precisa irrigar";
      texto = "Umidade adequada, solo em boas condições.";
    }
    alerta.className = `alerta ${tipo}`.trim();
    campo(el, "alerta-titulo").textContent = titulo;
    campo(el, "alerta-texto").textContent = texto;
    const alertaIcone = campo(el, "alerta-icone");
    if (alertaIcone.dataset.atual !== ic) { alertaIcone.innerHTML = icone(ic); alertaIcone.dataset.atual = ic; }

    // simulação
    const sim = campo(el, "simulador");
    sim.hidden = geral.modo !== "simulacao" || !online;
    if (!sim.hidden && local == null) {
      $("input", sim).value = c.umidade ?? 0;
      $("input", sim).style.setProperty("--pct", `${c.umidade ?? 0}%`);
      $("output", sim).textContent = `${c.umidade ?? 0}%`;
    }
  }

  function renderCanteiros(geral) {
    const lista = $("#lista-canteiros");
    const existentes = new Map($$(".card", lista).map((el) => [el.dataset.id, el]));
    geral.canteiros.forEach((c, i) => {
      let el = existentes.get(c.id);
      if (!el) el = montarCard(c);
      existentes.delete(c.id);
      if (lista.children[i] !== el) lista.insertBefore(el, lista.children[i] || null);
      atualizarCard(el, c, i, geral);
    });
    existentes.forEach((el) => el.remove());
    $("#vazio").hidden = geral.canteiros.length > 0;
  }

  // ------------------------------------------------------------------
  // topo, resumo e programa
  // ------------------------------------------------------------------

  function renderTopo(geral) {
    const ponto = $("#status-ponto");
    const texto = $("#status-texto");
    ponto.className = "ponto";
    if (!estado.servidorOnline) {
      ponto.classList.add("vermelho"); texto.textContent = "Servidor offline";
    } else if (geral.modo === "simulacao") {
      ponto.classList.add("amarelo"); texto.textContent = "Simulação";
    } else if (geral.esp32.online) {
      ponto.classList.add("verde"); texto.textContent = "Conectado";
    } else {
      ponto.classList.add("vermelho"); texto.textContent = "ESP32 offline";
    }

    $("#qtd-locais").textContent = geral.canteiros.length;
    $("#qtd-sensores").textContent = new Set(geral.canteiros.map((c) => c.pino_sensor)).size;
    $("#qtd-irrigar").textContent = estado.servidorOnline ? geral.canteiros.filter((c) => situacaoCanteiro(c).seco).length : "--";
    const intervalo = textoIntervalo(geral.ajustes.intervalo);
    $("#resumo-intervalo").textContent = intervalo;
    $("#rodape-intervalo").textContent = intervalo;
  }

  function renderPrograma(geral) {
    const p = geral.programa;
    const online = estado.servidorOnline;
    const chip = $("#programa-status");
    chip.className = "chip" + (online && p.rodando ? " rodando" : "") + (online && p.erro ? " erro" : "");
    chip.textContent = !online ? "Sem dados" : p.rodando ? (p.erro ? "Rodando, com erro" : "Rodando") : p.erro ? "Com erro" : "Parado";
    $("#programa-origem").textContent = p.origem === "gerado" ? "gerado pelos canteiros" : "código personalizado";
    const erro = $("#programa-erro");
    erro.hidden = !p.erro;
    erro.textContent = online && p.rodando && p.erro
      ? `O programa atual tem erro e não entrou em execução. Segue rodando a versão anterior. ${p.erro}`
      : p.erro || "";
    $("#btn-rodar").disabled = p.rodando || !online;
    $("#btn-parar").disabled = !p.rodando || !online;
    $("#btn-voltar-gerado").hidden = p.origem !== "personalizado" && !estado.editorSujo;

    const assinatura = `${p.origem}|${p.iniciado_em}|${geral.canteiros.map((c) => c.id + c.variavel + c.minimo + c.pino_sensor + c.pino_bomba).join()}|${geral.ajustes.intervalo}|${geral.ajustes.tempo_rega}`;
    if (assinatura !== estado.assinaturaPrograma) {
      estado.assinaturaPrograma = assinatura;
      if (!estado.editorSujo) carregarPrograma();
    }
  }

  function renderConsole(mensagens) {
    if (!mensagens.length) {
      if (!estado.console.length) $("#console").innerHTML = '<li class="vazio-console">Nada por aqui ainda.</li>';
      return;
    }
    const lista = $("#console");
    if (!estado.console.length) lista.innerHTML = "";
    const perto = lista.scrollHeight - lista.scrollTop - lista.clientHeight < 40;
    for (const m of mensagens) {
      estado.console.push(m);
      const li = document.createElement("li");
      li.className = `t-${m.tipo}`;
      const tempo = document.createElement("time");
      tempo.textContent = hora(m.t, true);
      const txt = document.createElement("span");
      txt.textContent = (m.tipo === "say" ? "› " : m.tipo === "erro" ? "✖ " : m.tipo === "aviso" ? "! " : "") + m.texto;
      li.append(tempo, txt);
      lista.append(li);
    }
    while (estado.console.length > 300) { estado.console.shift(); lista.firstElementChild.remove(); }
    if (perto) lista.scrollTop = lista.scrollHeight;
  }

  // ------------------------------------------------------------------
  // laço de atualização
  // ------------------------------------------------------------------

  function marcarConexao(online) {
    const mudou = estado.servidorOnline !== online;
    estado.servidorOnline = online;
    if (online) estado.ultimoContato = Date.now() / 1000;
    document.body.classList.toggle("sem-servidor", !online);
    const aviso = $("#aviso-desatualizado");
    aviso.hidden = online;
    if (!online) {
      aviso.textContent = estado.ultimoContato
        ? `Sem contato com o servidor desde ${hora(estado.ultimoContato, true)}. Os dados abaixo não são atuais.`
        : "Sem contato com o servidor.";
    }
    if (mudou) toast(online ? "Conexão com o servidor de volta." : "Sem contato com o servidor.", !online);
  }

  // Avisa só o que muda: a lista em si não é região viva, e o #toast é role="status".
  const ANUNCIO_MIN_MS = 30000; // por canteiro, para a simulação acelerada não virar enxurrada
  const anuncios = new Map(); // id -> { fase, t }

  function anunciarMudancas(geral) {
    if ($("#toast").textContent) return; // espera o aviso atual sumir; a comparação fica para o próximo ciclo
    const agora = Date.now();
    const frases = [];
    for (const c of geral.canteiros) {
      const fase = c.irrigando ? "regando" : c.situacao === "sem_sinal" ? "sem_sinal" : "normal";
      const antes = anuncios.get(c.id);
      if (antes?.fase === fase) continue;
      const fala = antes && fase !== "normal" && agora - antes.t >= ANUNCIO_MIN_MS;
      anuncios.set(c.id, { fase, t: fala ? agora : antes?.t ?? 0 });
      if (fala) frases.push(fase === "regando" ? `Regando ${c.nome}.` : `Sensor de ${c.nome} sem sinal.`);
    }
    if (frases.length) toast(frases.join(" "));
  }

  async function atualizar() {
    try {
      const { dados } = await api("GET", `/api/estado?desde=${estado.seq}`);
      marcarConexao(true);
      // servidor reiniciado: a sessão mudou (a primeira resposta só registra a sessão)
      if (estado.sessao && dados.sessao !== estado.sessao) {
        estado.sessao = dados.sessao;
        estado.seq = 0;
        estado.console = [];
        estado.assinaturaPrograma = "";
        $("#console").innerHTML = "";
        return atualizar();
      }
      estado.sessao = dados.sessao;
      estado.atual = dados;
      estado.seq = dados.seq;
      renderTopo(dados);
      renderCanteiros(dados);
      renderPrograma(dados);
      renderConsole(dados.console);
      atualizarDialogos(dados);
      anunciarMudancas(dados);
    } catch {
      marcarConexao(false);
      if (estado.atual) {
        renderTopo(estado.atual);
        renderCanteiros(estado.atual);
        renderPrograma(estado.atual);
        atualizarDialogos(estado.atual);
      } else { $("#status-ponto").className = "ponto vermelho"; $("#status-texto").textContent = "Servidor offline"; }
    }
  }

  function laco() {
    atualizar().finally(() => setTimeout(laco, 1000));
  }

  setInterval(() => {
    const d = new Date();
    $("#relogio").textContent = `${dois(d.getHours())}:${dois(d.getMinutes())}`;
  }, 1000);

  // ------------------------------------------------------------------
  // programa: código, tokens, AST
  // ------------------------------------------------------------------

  const editor = $("#editor");
  const gutter = $("#editor-linhas");

  function desenharLinhas(compilacao) {
    const total = editor.value.split("\n").length;
    const erros = new Set((compilacao?.erros || []).map((e) => e.linha));
    const avisos = new Set((compilacao?.avisos || []).map((e) => e.linha));
    let html = "";
    for (let i = 1; i <= total; i++) {
      const cls = erros.has(i) ? "linha-erro" : avisos.has(i) ? "linha-aviso" : "";
      html += (cls ? `<span class="${cls}">${i}</span>` : i) + "\n";
    }
    gutter.innerHTML = html;
    gutter.scrollTop = editor.scrollTop;
  }

  function mostrarCompilacao(c) {
    estado.compilacao = c;
    const lista = $("#diagnosticos");
    lista.innerHTML = "";
    const add = (cls, rotulo, texto) => {
      const li = document.createElement("li");
      li.className = cls;
      const b = document.createElement("b");
      b.textContent = rotulo;
      const s = document.createElement("span");
      s.textContent = texto;
      li.append(b, s);
      lista.append(li);
    };
    const FASES = { "Léxica": "Erro léxico", "Sintática": "Erro sintático", "Semântica": "Erro semântico" };
    for (const e of c.erros) {
      const prefixo = e.linha && !e.texto.startsWith("Linha") ? `Linha ${e.linha}: ` : "";
      add("d-erro", FASES[e.fase] || "Erro", prefixo + e.texto);
    }
    for (const a of c.avisos) add("d-aviso", "Aviso", (a.linha ? `Linha ${a.linha}: ` : "") + a.texto);
    if (c.ok) add("d-ok", "OK", `Léxico, sintático e semântico sem erros. ${c.tokens.length} tokens.`);

    $("#tokens").innerHTML = c.tokens.map((t, i) =>
      `<tr><td>${i + 1}</td><td>${t.tipo}</td><td>${escapar(String(t.valor))}</td><td>${t.linha}:${t.coluna}</td></tr>`).join("");
    $("#ast").textContent = c.ast || "A AST aparece quando o código passa pela análise sintática.";
    desenharLinhas(c);
  }

  function escapar(s) {
    return s.replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
  }

  async function carregarPrograma() {
    try {
      const { dados } = await api("GET", "/api/programa");
      if (estado.editorSujo) return;
      const pos = editor.scrollTop;
      editor.value = dados.codigo;
      editor.scrollTop = pos;
      mostrarCompilacao(dados.compilacao);
    } catch (e) { toast(e.message, true); }
  }

  let timerCompilar;
  async function verificar() {
    clearTimeout(timerCompilar);
    try {
      const { dados } = await api("POST", "/api/programa/compilar", { codigo: editor.value });
      mostrarCompilacao(dados);
      return dados;
    } catch (e) { toast(e.message, true); }
  }

  editor.addEventListener("input", () => {
    estado.editorSujo = true;
    $("#btn-voltar-gerado").hidden = false;
    desenharLinhas(estado.compilacao);
    clearTimeout(timerCompilar);
    timerCompilar = setTimeout(verificar, 600);
  });
  editor.addEventListener("scroll", () => { gutter.scrollTop = editor.scrollTop; });
  let tabLiberado = false; // Esc solta o próximo Tab para sair do editor
  editor.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") { tabLiberado = e.key === "Escape"; return; }
    if (e.shiftKey || tabLiberado) { tabLiberado = false; return; }
    e.preventDefault();
    document.execCommand("insertText", false, "    ");
  });
  editor.addEventListener("blur", () => { tabLiberado = false; });

  window.addEventListener("beforeunload", (e) => {
    if (estado.editorSujo) { e.preventDefault(); e.returnValue = ""; }
  });

  $("#btn-verificar").addEventListener("click", verificar);

  $("#btn-rodar-codigo").addEventListener("click", async () => {
    try {
      const { status, dados } = await api("POST", "/api/programa/rodar", { codigo: editor.value });
      mostrarCompilacao(dados);
      if (status === 422) { toast("O código tem erros. Veja a lista abaixo do editor.", true); return; }
      estado.editorSujo = false;
      toast("Seu código está rodando.");
      atualizar();
    } catch (e) { toast(e.message, true); }
  });

  $("#btn-voltar-gerado").addEventListener("click", async () => {
    try {
      estado.editorSujo = false;
      await api("POST", "/api/programa/gerado");
      await carregarPrograma();
      toast("Programa gerado pelos canteiros restaurado.");
      atualizar();
    } catch (e) { toast(e.message, true); }
  });

  $("#btn-rodar").addEventListener("click", async () => {
    try {
      const { status, dados } = await api("POST", "/api/programa/rodar", {});
      if (status === 422) {
        mostrarCompilacao(dados);
        trocarAba("codigo");
        toast("O programa não compilou. Veja os erros na aba Código.", true);
      }
      atualizar();
    } catch (e) { toast(e.message, true); }
  });

  $("#btn-parar").addEventListener("click", async () => {
    try { await api("POST", "/api/programa/parar"); atualizar(); } catch (e) { toast(e.message, true); }
  });

  function trocarAba(nome) {
    estado.aba = nome;
    $$(".aba").forEach((b) => {
      const ativa = b.dataset.aba === nome;
      b.classList.toggle("ativa", ativa);
      b.setAttribute("aria-selected", ativa);
    });
    $$(".painel").forEach((p) => (p.hidden = p.dataset.painel !== nome));
    if (nome !== "console" && !estado.compilacao) carregarPrograma();
    if (nome === "codigo") desenharLinhas(estado.compilacao);
  }
  $$(".aba").forEach((b) => b.addEventListener("click", () => trocarAba(b.dataset.aba)));

  // ------------------------------------------------------------------
  // diálogo do canteiro
  // ------------------------------------------------------------------

  // Enter segurado repete o envio: cada formulário grava uma vez por vez
  let salvando = false;

  const dlgCanteiro = $("#dlg-canteiro");
  const formCanteiro = $("#form-canteiro");

  function abrirCanteiro(c) {
    estado.editandoId = c ? c.id : null;
    $("#dlg-canteiro-titulo").textContent = c ? "Editar local" : "Adicionar sensor";
    $("#erro-canteiro").hidden = true;
    const usados = new Set((estado.atual?.canteiros || []).filter((x) => !c || x.id !== c.id).map((x) => x.pino_sensor));
    const livre = PINOS_SENSOR.find((p) => !usados.has(p)) ?? 34;
    formCanteiro.nome.value = c ? c.nome : "";
    opcoesPinos(formCanteiro.pino_sensor, PINOS_SENSOR, c ? c.pino_sensor : livre);
    const bombaPadrao = estado.atual?.canteiros[0]?.pino_bomba ?? 26;
    opcoesPinos(formCanteiro.pino_bomba, PINOS_BOMBA, c ? c.pino_bomba : bombaPadrao);
    formCanteiro.minimo.value = c ? c.minimo : 30;
    $("#minimo-valor").textContent = `${formCanteiro.minimo.value}%`;
    formCanteiro.seco.value = c?.seco ?? "";
    formCanteiro.molhado.value = c?.molhado ?? "";
    $("#btn-testar-bomba").hidden = !c;
    $(".calibracao").open = false;
    atualizarDialogos(estado.atual);
    dlgCanteiro.showModal();
    if (!c) formCanteiro.nome.focus();
  }

  formCanteiro.minimo.addEventListener("input", () => {
    $("#minimo-valor").textContent = `${formCanteiro.minimo.value}%`;
  });

  $$("[data-capturar]").forEach((b) => b.addEventListener("click", () => {
    const bruto = $("#leitura-bruta").dataset.valor;
    if (!bruto) { toast("Ainda não há leitura desse sensor.", true); return; }
    formCanteiro[b.dataset.capturar].value = bruto;
  }));

  $("#btn-testar-bomba").addEventListener("click", async () => {
    try {
      await api("POST", "/api/bomba/testar", { pino: Number(formCanteiro.pino_bomba.value), segundos: 3 });
      toast("Bomba ligada por 3 segundos.");
    } catch (e) { toast(e.message, true); }
  });

  formCanteiro.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (salvando) return;
    salvando = true;
    const corpo = {
      nome: formCanteiro.nome.value.trim(),
      pino_sensor: Number(formCanteiro.pino_sensor.value),
      pino_bomba: Number(formCanteiro.pino_bomba.value),
      minimo: Number(formCanteiro.minimo.value),
      seco: formCanteiro.seco.value.trim() || null,
      molhado: formCanteiro.molhado.value.trim() || null,
    };
    try {
      if (estado.editandoId) await api("PUT", `/api/canteiros/${estado.editandoId}`, corpo);
      else await api("POST", "/api/canteiros", corpo);
      dlgCanteiro.close();
      toast(estado.editandoId ? "Local atualizado." : "Sensor adicionado.");
      atualizar();
    } catch (err) {
      const el = $("#erro-canteiro");
      el.textContent = err.message;
      el.hidden = false;
    } finally { salvando = false; }
  });

  // ------------------------------------------------------------------
  // diálogo do intervalo
  // ------------------------------------------------------------------

  const dlgIntervalo = $("#dlg-intervalo");
  const formIntervalo = $("#form-intervalo");

  function abrirIntervalo() {
    const atual = estado.atual?.ajustes.intervalo ?? 120;
    const lista = INTERVALOS.some(([s]) => s === atual) ? INTERVALOS : [...INTERVALOS, [atual, textoIntervalo(atual)]];
    $("#opcoes-intervalo").innerHTML = lista.map(([s, rotulo]) =>
      `<label><input type="radio" name="intervalo" value="${s}" ${s === atual ? "checked" : ""}><span>${rotulo}</span></label>`).join("");
    formIntervalo.tempo_rega.value = estado.atual?.ajustes.tempo_rega ?? 10;
    $("#erro-intervalo").hidden = true;
    dlgIntervalo.showModal();
  }

  formIntervalo.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (salvando) return;
    salvando = true;
    try {
      await api("PUT", "/api/ajustes", {
        intervalo: Number(formIntervalo.intervalo.value),
        tempo_rega: Number(formIntervalo.tempo_rega.value),
      });
      dlgIntervalo.close();
      toast("Frequência atualizada.");
      atualizar();
    } catch (err) {
      $("#erro-intervalo").textContent = err.message;
      $("#erro-intervalo").hidden = false;
    } finally { salvando = false; }
  });

  // ------------------------------------------------------------------
  // diálogo de conexão
  // ------------------------------------------------------------------

  const dlgConexao = $("#dlg-conexao");
  const formConexao = $("#form-conexao");

  function abrirConexao() {
    const a = estado.atual;
    if (!a) return;
    formConexao.modo.value = a.modo;
    formConexao.fator_tempo.value = String(a.ajustes.fator_tempo);
    if (!formConexao.fator_tempo.value) formConexao.fator_tempo.value = "1";
    formConexao.limite_bomba.value = a.ajustes.limite_bomba;
    formConexao.cal_seco.value = a.ajustes.calibracao_padrao.seco;
    formConexao.cal_molhado.value = a.ajustes.calibracao_padrao.molhado;
    formConexao.rodar_ao_iniciar.checked = a.ajustes.rodar_ao_iniciar;
    $("#erro-conexao").hidden = true;
    atualizarDialogos(a);
    dlgConexao.showModal();
  }

  $$('input[name="modo"]', formConexao).forEach((r) => r.addEventListener("change", () => atualizarDialogos(estado.atual)));

  formConexao.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (salvando) return;
    salvando = true;
    try {
      await api("PUT", "/api/ajustes", {
        modo: formConexao.modo.value,
        fator_tempo: Number(formConexao.fator_tempo.value),
        limite_bomba: Number(formConexao.limite_bomba.value),
        calibracao_padrao: { seco: Number(formConexao.cal_seco.value), molhado: Number(formConexao.cal_molhado.value) },
        rodar_ao_iniciar: formConexao.rodar_ao_iniciar.checked,
      });
      dlgConexao.close();
      toast("Ajustes salvos.");
      atualizar();
    } catch (err) {
      $("#erro-conexao").textContent = err.message;
      $("#erro-conexao").hidden = false;
    } finally { salvando = false; }
  });

  function atualizarDialogos(a) {
    if (!a) return;
    if (dlgCanteiro.open) {
      const pino = Number(formCanteiro.pino_sensor.value);
      const c = a.canteiros.find((x) => x.pino_sensor === pino);
      const el = $("#leitura-bruta");
      const valor = estado.servidorOnline && c && c.bruto != null ? c.bruto : null;
      el.textContent = valor != null ? `${valor} (${a.modo === "simulacao" ? "simulado" : "GPIO " + pino})` : "sem leitura";
      el.dataset.valor = valor ?? "";
    }
    if (dlgConexao.open) {
      const modo = formConexao.modo.value;
      $("#campo-fator").hidden = modo !== "simulacao";
      const esp = a.esp32;
      const local = ["localhost", "127.0.0.1"].includes(location.hostname);
      const endereco = local ? "o endereço mostrado no terminal do servidor" : `<code>${escapar(location.origin)}</code>`;
      $("#info-esp32").innerHTML = modo === "simulacao"
        ? `<div>Na simulação a umidade é inventada pelo servidor e sobe enquanto a bomba está ligada. Use os controles <strong>Simular umidade</strong> em cada local.</div>`
        : `<div class="linha"><span>ESP32</span><strong>${!estado.servidorOnline ? "Sem dados" : esp.online ? "Conectado" : "Desconectado"}</strong></div>
           ${esp.ip ? `<div class="linha"><span>IP da placa</span><strong>${escapar(esp.ip)}</strong></div>` : ""}
           ${esp.rssi ? `<div class="linha"><span>Sinal Wi-Fi</span><strong>${escapar(String(esp.rssi))} dBm</strong></div>` : ""}
           ${esp.ultimo ? `<div class="linha"><span>Último contato</span><strong>${hora(esp.ultimo, true)}</strong></div>` : ""}
           <div>No firmware, coloque em <code>SERVIDOR</code> ${endereco}.</div>`;
    }
  }

  // ------------------------------------------------------------------
  // remover
  // ------------------------------------------------------------------

  const dlgConfirmar = $("#dlg-confirmar");
  function confirmar(titulo, texto) {
    $("#confirmar-titulo").textContent = titulo;
    $("#confirmar-texto").textContent = texto;
    dlgConfirmar.returnValue = "";
    dlgConfirmar.showModal();
    return new Promise((ok) => dlgConfirmar.addEventListener("close", () => ok(dlgConfirmar.returnValue === "sim"), { once: true }));
  }

  $("#lista-canteiros").addEventListener("click", async (e) => {
    const botao = e.target.closest("[data-acao]");
    if (!botao) return;
    const id = botao.closest(".card").dataset.id;
    const c = estado.atual.canteiros.find((x) => x.id === id);
    if (!c) return;
    if (botao.dataset.acao === "editar") abrirCanteiro(c);
    if (botao.dataset.acao === "remover") {
      if (!(await confirmar(`Remover ${c.nome}?`, "O sensor deixa de ser lido e o programa é gerado de novo sem ele."))) return;
      try { await api("DELETE", `/api/canteiros/${id}`); toast("Local removido."); atualizar(); }
      catch (err) { toast(err.message, true); }
    }
  });

  // ------------------------------------------------------------------
  // início
  // ------------------------------------------------------------------

  $("#icone-bloco").innerHTML = blocoPixel();
  preencherIcones();
  $$("[data-fechar]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));
  $("#btn-adicionar").addEventListener("click", () => abrirCanteiro(null));
  $("#btn-intervalo").addEventListener("click", abrirIntervalo);
  $("#btn-intervalo-resumo").addEventListener("click", abrirIntervalo);
  $("#btn-conexao").addEventListener("click", abrirConexao);
  formCanteiro.pino_sensor.addEventListener("change", () => atualizarDialogos(estado.atual));
  laco();
})();
