window.Fuso = (function () {
  const ZONAS = [
    { rotulo: "🇧🇷 Brasil", tz: "America/Sao_Paulo" },
    { rotulo: "🇮🇪 Irlanda", tz: "Europe/Dublin" },
  ];

  function formatar(data, tz) {
    return new Intl.DateTimeFormat("pt-BR", {
      timeZone: tz, weekday: "short", day: "2-digit", month: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(data);
  }

  function linhas(iso) {
    const data = new Date(iso);
    const saida = ZONAS.map(z => ({ rotulo: z.rotulo, texto: formatar(data, z.tz) }));
    const meu = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (!ZONAS.some(z => z.tz === meu)) {
      saida.push({ rotulo: "No seu horário", texto: formatar(data, meu) });
    }
    return saida;
  }

  return { linhas };
})();