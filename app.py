import os
import re
import requests
import random
import uuid
from datetime import timedelta, date, datetime, timezone  
from flask import Flask, render_template, request, redirect, session, url_for, send_from_directory
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET")
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024
app.permanent_session_lifetime = timedelta(days=365)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.getenv("RENDER")),
)

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


@app.route("/entrar", methods=["GET", "POST"])
def entrar():
    erro = None
    if request.method == "POST":
        nome = request.form["nome"].strip()
        codigo = request.form["codigo"].strip()
        if codigo != os.getenv("CODIGO_CLUBE"):
            erro = "Código do clube incorreto."
        elif not nome:
            erro = "Escreva o seu nome."
        else:
            existente = supabase.table("membros").select("*").eq("nome", nome).execute().data
            if existente:
                membro = existente[0]
            else:
                membro = supabase.table("membros").insert({"nome": nome}).execute().data[0]
            session.permanent = True
            session["membro_id"] = membro["id"]
            session["nome"] = membro["nome"]
            return redirect(url_for("inicio"))
    return render_template("entrar.html", erro=erro)


@app.route("/")
def inicio():
    if "membro_id" not in session:
        return redirect(url_for("entrar"))
    achados = supabase.table("livros").select("*").eq("do_mes", True).limit(1).execute().data
    livro = achados[0] if achados else None
    return render_template("inicio.html", nome=session["nome"], livro=livro)


@app.route("/sugestoes")
def sugestoes():
    if "membro_id" not in session:
        return redirect(url_for("entrar"))
    return render_template("sugestoes.html")


@app.route("/avaliacoes")
def avaliacoes():
    if "membro_id" not in session:
        return redirect(url_for("entrar"))
    return render_template("avaliacoes.html")

VIBES = {
    "terror": ["subject:horror", "livro de terror", "romance de terror e horror"],
    "romance": ["subject:romance", "romance de amor", "romance contemporâneo"],
    "suspense": ["subject:thriller", "livro de suspense", "thriller psicológico"],
    "misterio": ["subject:mystery", "livro de mistério", "romance policial detetive"],
    "fantasia": ["subject:fantasy", "livro de fantasia", "fantasia épica magia"],
    "ficcao": ["subject:science fiction", "ficção científica", "ficção científica distopia"],
    "gotico": ["subject:gothic", "romance gótico", "literatura gótica"],
    "classicos": ["subject:classics", "clássicos da literatura", "literatura brasileira clássica"],
    "humor": ["subject:humor", "livro de humor", "comédia romântica"],
    "drama": ["subject:drama", "romance dramático", "ficção contemporânea drama"],
}

URL_LIVROS = "https://www.googleapis.com/books/v1/volumes"


class ErroGoogle(Exception):
    pass


def pedir_livros(q, extra=None):
    params = {"q": q, "printType": "books", "key": os.getenv("GOOGLE_BOOKS_KEY")}
    if extra:
        params.update(extra)
    r = requests.get(URL_LIVROS, params=params, timeout=8)
    if r.status_code != 200:
        raise ErroGoogle(r.status_code)
    return r.json().get("items", [])


@app.route("/buscar-livro")
def buscar_livro():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401

    vibe = request.args.get("vibe", "").strip()
    titulo = request.args.get("q", "").strip()
    max_pag = request.args.get("max_paginas", type=int)
    por_vibe = vibe in VIBES

    try:
        if por_vibe:
            itens = []
            for q in random.sample(VIBES[vibe], 2):
                extra = {"maxResults": 40, "langRestrict": "pt", "startIndex": random.choice([0, 0, 40])}
                achados = pedir_livros(q, extra)
                if not achados and extra["startIndex"]:
                    extra["startIndex"] = 0
                    achados = pedir_livros(q, extra)
                itens += achados
        elif len(titulo) >= 3:
            itens = pedir_livros(titulo, {"maxResults": 10})
        else:
            return {"resultados": []}
    except ErroGoogle as e:
        return {"erro": f"O Google Books recusou a busca (código {e.args[0]})."}, 502
    except Exception:
        return {"erro": "Não consegui falar com o Google Books agora."}, 502

    resultados, vistos = [], set()
    for i in itens:
        info = i.get("volumeInfo", {})
        nome = info.get("title", "Sem título")
        capa = info.get("imageLinks", {}).get("thumbnail", "").replace("http://", "https://")
        paginas = info.get("pageCount") or None

        if nome.lower() in vistos:
            continue
        if por_vibe and not (info.get("language") or "pt").lower().startswith("pt"):
            continue
        if max_pag and (not paginas or paginas > max_pag):
            continue
        vistos.add(nome.lower())

        resumo = re.sub(r"<[^>]+>", " ", info.get("description", "")).strip()
        resultados.append({
            "titulo": nome,
            "autor": ", ".join(info.get("authors", [])) or "Autor desconhecido",
            "resumo": resumo,
            "paginas": paginas,
            "capa": capa,
        })

    if por_vibe:
        random.shuffle(resultados)
        resultados = resultados[:12]
    return {"resultados": resultados}

@app.route("/sugestoes/salvar", methods=["POST"])
def salvar_sugestao():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    d = request.get_json(silent=True) or {}
    titulo = str(d.get("titulo", "")).strip()[:200]
    if not titulo:
        return {"erro": "Livro sem título."}, 400

    minhas = supabase.table("sugestoes").select("id, titulo").eq("membro_id", session["membro_id"]).execute().data
    if len(minhas) >= 3:
        return {"erro": "Você já usou suas 3 sugestões. Apague uma para trocar."}, 400
    if any(m["titulo"].lower() == titulo.lower() for m in minhas):
        return {"erro": "Você já sugeriu esse livro."}, 400

    paginas = d.get("paginas")
    paginas = paginas if isinstance(paginas, int) and paginas > 0 else None
    capa = str(d.get("capa", ""))[:500]
    if not capa.startswith("https://"):
        capa = ""

    supabase.table("sugestoes").insert({
        "membro_id": session["membro_id"],
        "titulo": titulo,
        "autor": str(d.get("autor", ""))[:200],
        "capa": capa,
        "resumo": str(d.get("resumo", ""))[:3000],
        "paginas": paginas,
    }).execute()
    return {"ok": True}


@app.route("/sugestoes/minhas")
def minhas_sugestoes():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    dados = supabase.table("sugestoes").select("*").eq("membro_id", session["membro_id"]).order("id").execute().data
    return {"sugestoes": dados}


@app.route("/sugestoes/<int:sugestao_id>", methods=["DELETE"])
def apagar_sugestao(sugestao_id):
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    supabase.table("sugestoes").delete().eq("id", sugestao_id).eq("membro_id", session["membro_id"]).execute()
    return {"ok": True}

def tipo_imagem(dados):
    if dados.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if dados.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if dados[:4] == b"RIFF" and dados[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None, None


@app.route("/sugestoes/manual", methods=["POST"])
def sugestao_manual():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401

    titulo = request.form.get("titulo", "").strip()[:200]
    if not titulo:
        return {"erro": "Escreva o título do livro."}, 400

    minhas = supabase.table("sugestoes").select("id, titulo").eq("membro_id", session["membro_id"]).execute().data
    if len(minhas) >= 3:
        return {"erro": "Você já usou suas 3 sugestões. Apague uma para trocar."}, 400
    if any(m["titulo"].lower() == titulo.lower() for m in minhas):
        return {"erro": "Você já sugeriu esse livro."}, 400

    paginas = request.form.get("paginas", type=int)
    if paginas is not None and not (1 <= paginas <= 5000):
        paginas = None

    capa_url = ""
    arquivo = request.files.get("capa")
    if arquivo:
        limite = 2 * 1024 * 1024
        dados = arquivo.read(limite + 1)
        if len(dados) > limite:
            return {"erro": "A imagem da capa é grande demais."}, 400
        mime, ext = tipo_imagem(dados)
        if not mime:
            return {"erro": "Use uma imagem JPG, PNG ou WEBP."}, 400
        caminho = f"capas/{session['membro_id']}-{uuid.uuid4().hex}.{ext}"
        try:
            supabase.storage.from_("imagens").upload(caminho, dados, {"content-type": mime})
            capa_url = supabase.storage.from_("imagens").get_public_url(caminho)
        except Exception as e:
            print("ERRO UPLOAD:", e)
            return {"erro": "Não consegui enviar a capa agora. Tente de novo."}, 502

    supabase.table("sugestoes").insert({
        "membro_id": session["membro_id"],
        "titulo": titulo,
        "autor": request.form.get("autor", "").strip()[:200],
        "capa": capa_url,
        "resumo": request.form.get("resumo", "").strip()[:3000],
        "paginas": paginas,
    }).execute()
    return {"ok": True}

def eh_admin():
    admin = os.getenv("ADMIN_NOME", "").strip().lower()
    return bool(admin) and session.get("nome", "").strip().lower() == admin


@app.route("/votacao")
def votacao():
    if "membro_id" not in session:
        return redirect(url_for("entrar"))
    return render_template("votacao.html")


@app.route("/votacao/dados")
def votacao_dados():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    sugs = supabase.table("sugestoes").select("*, membros(nome)").order("id").execute().data
    votos = supabase.table("votos").select("membro_id, sugestao_id").execute().data
    total = supabase.table("membros").select("id", count="exact").execute().count

    contagem = {}
    for v in votos:
        contagem[v["sugestao_id"]] = contagem.get(v["sugestao_id"], 0) + 1
    meu = next((v["sugestao_id"] for v in votos if v["membro_id"] == session["membro_id"]), None)

    itens = []
    for s in sugs:
        itens.append({
            "id": s["id"],
            "titulo": s["titulo"],
            "autor": s.get("autor") or "Autor desconhecido",
            "capa": s.get("capa") or "",
            "resumo": s.get("resumo") or "",
            "paginas": s.get("paginas"),
            "por": (s.get("membros") or {}).get("nome", ""),
            "votos": contagem.get(s["id"], 0),
        })
    return {"sugestoes": itens, "meu_voto": meu, "votaram": len(votos), "total": total, "admin": eh_admin()}


@app.route("/votacao/votar", methods=["POST"])
def votar():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    d = request.get_json(silent=True) or {}
    sid = d.get("sugestao_id")
    if not isinstance(sid, int):
        return {"erro": "Sugestão inválida."}, 400
    if not supabase.table("sugestoes").select("id").eq("id", sid).execute().data:
        return {"erro": "Essa sugestão não existe mais."}, 404
    supabase.table("votos").upsert(
        {"membro_id": session["membro_id"], "sugestao_id": sid}, on_conflict="membro_id"
    ).execute()
    return {"ok": True}


@app.route("/votacao/encerrar", methods=["POST"])
def encerrar_votacao():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    if not eh_admin():
        return {"erro": "Só quem organiza o clube pode encerrar a votação."}, 403

    votos = supabase.table("votos").select("sugestao_id").execute().data
    if not votos:
        return {"erro": "Ainda não há votos."}, 400

    contagem = {}
    for v in votos:
        contagem[v["sugestao_id"]] = contagem.get(v["sugestao_id"], 0) + 1
    topo = max(contagem.values())
    empatadas = [sid for sid, n in contagem.items() if n == topo]
    vencedora = random.choice(empatadas)

    s = supabase.table("sugestoes").select("*").eq("id", vencedora).execute().data[0]
    supabase.table("livros").update({"do_mes": False}).eq("do_mes", True).execute()
    supabase.table("livros").insert({
        "titulo": s["titulo"], "autor": s.get("autor"), "capa": s.get("capa"),
        "resumo": s.get("resumo"), "paginas": s.get("paginas"), "do_mes": True,
    }).execute()
    supabase.table("votos").delete().neq("id", 0).execute()
    supabase.table("sugestoes").delete().eq("id", vencedora).execute()
    return {"ok": True, "titulo": s["titulo"], "empate": len(empatadas) > 1}

@app.context_processor
def dados_do_membro():
    if "membro_id" not in session:
        return {}
    try:
        r = supabase.table("membros").select("foto").eq("id", session["membro_id"]).execute().data
        foto = r[0]["foto"] if r else ""
    except Exception:
        foto = ""
    return {"minha_foto": foto or ""}


@app.route("/perfil")
def perfil():
    if "membro_id" not in session:
        return redirect(url_for("entrar"))
    return render_template("perfil.html", nome=session["nome"])


@app.route("/perfil/foto", methods=["POST"])
def salvar_foto():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    arquivo = request.files.get("foto")
    if not arquivo:
        return {"erro": "Escolha uma imagem."}, 400
    limite = 2 * 1024 * 1024
    dados = arquivo.read(limite + 1)
    if len(dados) > limite:
        return {"erro": "A imagem é grande demais."}, 400
    mime, ext = tipo_imagem(dados)
    if not mime:
        return {"erro": "Use uma imagem JPG, PNG ou WEBP."}, 400
    caminho = f"fotos/{session['membro_id']}-{uuid.uuid4().hex}.{ext}"
    try:
        supabase.storage.from_("imagens").upload(caminho, dados, {"content-type": mime})
        url = supabase.storage.from_("imagens").get_public_url(caminho)
    except Exception as e:
        print("ERRO UPLOAD FOTO:", e)
        return {"erro": "Não consegui enviar a foto agora. Tente de novo."}, 502
    supabase.table("membros").update({"foto": url}).eq("id", session["membro_id"]).execute()
    return {"ok": True}


@app.route("/perfil/foto", methods=["DELETE"])
def remover_foto():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    supabase.table("membros").update({"foto": None}).eq("id", session["membro_id"]).execute()
    return {"ok": True}

def livro_do_mes():
    achados = supabase.table("livros").select("*").eq("do_mes", True).limit(1).execute().data
    return achados[0] if achados else None


@app.route("/leitura/dados")
def leitura_dados():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    livro = livro_do_mes()
    if not livro:
        return {"livro": None}
    prog = supabase.table("progresso").select("membro_id, pagina_atual").eq("livro_id", livro["id"]).execute().data
    por_membro = {p["membro_id"]: (p["pagina_atual"] or 0) for p in prog}
    membros = supabase.table("membros").select("id, nome, foto").order("nome").execute().data
    grupo = [{
        "nome": m["nome"],
        "foto": m.get("foto") or "",
        "pagina": por_membro.get(m["id"], 0),
        "sou_eu": m["id"] == session["membro_id"],
    } for m in membros]
    return {
        "livro": {
            "titulo": livro["titulo"],
            "paginas": livro.get("paginas"),
            "data_inicio": livro.get("data_inicio"),
            "data_fim": livro.get("data_fim"),
        },
        "minha_pagina": por_membro.get(session["membro_id"], 0),
        "grupo": grupo,
        "admin": eh_admin(),
    }


@app.route("/leitura/progresso", methods=["POST"])
def salvar_progresso():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    livro = livro_do_mes()
    if not livro:
        return {"erro": "Ainda não há livro do mês."}, 400
    d = request.get_json(silent=True) or {}
    pagina = d.get("pagina")
    if not isinstance(pagina, int) or isinstance(pagina, bool) or pagina < 0:
        return {"erro": "Escreva um número de página válido."}, 400
    teto = livro.get("paginas") or 10000
    if pagina > teto:
        return {"erro": f"O livro tem {teto} páginas."}, 400
    supabase.table("progresso").upsert(
        {"livro_id": livro["id"], "membro_id": session["membro_id"], "pagina_atual": pagina},
        on_conflict="livro_id,membro_id",
    ).execute()
    return {"ok": True}


@app.route("/livro/prazo", methods=["POST"])
def definir_prazo():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    if not eh_admin():
        return {"erro": "Só quem organiza o clube pode definir o prazo."}, 403
    livro = livro_do_mes()
    if not livro:
        return {"erro": "Ainda não há livro do mês."}, 400
    d = request.get_json(silent=True) or {}
    try:
        inicio = date.fromisoformat(str(d.get("data_inicio", "")))
        fim = date.fromisoformat(str(d.get("data_fim", "")))
    except ValueError:
        return {"erro": "Escolha as duas datas."}, 400
    if fim < inicio:
        return {"erro": "A data final precisa ser depois da inicial."}, 400
    mudancas = {"data_inicio": inicio.isoformat(), "data_fim": fim.isoformat()}
    paginas = d.get("paginas")
    if isinstance(paginas, int) and not isinstance(paginas, bool) and 1 <= paginas <= 5000:
        mudancas["paginas"] = paginas
    supabase.table("livros").update(mudancas).eq("id", livro["id"]).execute()
    return {"ok": True}

@app.route("/avaliacoes/dados")
def avaliacoes_dados():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401

    livros = supabase.table("livros").select("id, titulo, autor, capa, do_mes").order("id", desc=True).execute().data
    if not livros:
        return {"livros": []}

    pedido = request.args.get("livro_id", type=int)
    escolhido = (
        next((l for l in livros if l["id"] == pedido), None)
        or next((l for l in livros if l["do_mes"]), None)
        or livros[0]
    )

    avs = (
        supabase.table("avaliacoes")
        .select("*, membros(nome, foto)")
        .eq("livro_id", escolhido["id"])
        .order("id", desc=True)
        .execute()
        .data
    )
    notas = [float(a["estrelas"] or 0) for a in avs]
    media = round(sum(notas) / len(notas), 1) if notas else None
    minha = next((a for a in avs if a["membro_id"] == session["membro_id"]), None)

    return {
        "livros": [{"id": l["id"], "titulo": l["titulo"], "do_mes": l["do_mes"]} for l in livros],
        "livro": escolhido,
        "media": media,
        "quantidade": len(notas),
        "minha": {"estrelas": float(minha["estrelas"] or 0), "resenha": minha.get("resenha") or ""} if minha else None,
        "avaliacoes": [{
            "nome": (a.get("membros") or {}).get("nome", ""),
            "foto": (a.get("membros") or {}).get("foto") or "",
            "estrelas": float(a["estrelas"] or 0),
            "resenha": a.get("resenha") or "",
            "sou_eu": a["membro_id"] == session["membro_id"],
        } for a in avs],
    }


@app.route("/avaliacoes/salvar", methods=["POST"])
def salvar_avaliacao():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    d = request.get_json(silent=True) or {}
    livro_id = d.get("livro_id")
    estrelas = d.get("estrelas")

    if not isinstance(livro_id, int) or isinstance(livro_id, bool):
        return {"erro": "Livro inválido."}, 400
    if (
        isinstance(estrelas, bool)
        or not isinstance(estrelas, (int, float))
        or not (0.5 <= estrelas <= 5)
        or (estrelas * 2) % 1 != 0
    ):
        return {"erro": "Escolha de 0,5 a 5 estrelas."}, 400
    if not supabase.table("livros").select("id").eq("id", livro_id).execute().data:
        return {"erro": "Esse livro não existe."}, 404

    resenha = str(d.get("resenha", "")).strip()[:2000]
    supabase.table("avaliacoes").upsert(
        {"livro_id": livro_id, "membro_id": session["membro_id"], "estrelas": estrelas, "resenha": resenha},
        on_conflict="livro_id,membro_id",
    ).execute()
    return {"ok": True}


@app.route("/avaliacoes/<int:livro_id>", methods=["DELETE"])
def remover_avaliacao(livro_id):
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    supabase.table("avaliacoes").delete().eq("livro_id", livro_id).eq("membro_id", session["membro_id"]).execute()
    return {"ok": True}

def agora_utc():
    return datetime.now(timezone.utc)


def iso_z(d):
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ler_data_utc(texto):
    d = datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("sem fuso")
    return d.astimezone(timezone.utc)


@app.route("/encontro")
def encontro():
    if "membro_id" not in session:
        return redirect(url_for("entrar"))
    return render_template("encontro.html")


@app.route("/encontro/dados")
def encontro_dados():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    livro = livro_do_mes()
    if not livro:
        return {"livro": None}

    membros = supabase.table("membros").select("id, nome").execute().data
    nomes = {m["id"]: m["nome"] for m in membros}
    total = len(membros)

    paginas = livro.get("paginas")
    prog = supabase.table("progresso").select("membro_id, pagina_atual").eq("livro_id", livro["id"]).execute().data
    terminaram = [p["membro_id"] for p in prog if paginas and (p["pagina_atual"] or 0) >= paginas]

    limite = iso_z(agora_utc() - timedelta(hours=3))
    encs = (
        supabase.table("encontros").select("*")
        .eq("livro_id", livro["id"]).gte("inicio", limite).order("inicio")
        .execute().data
    )
    ids = [e["id"] for e in encs]
    resp = supabase.table("disponibilidade").select("*").in_("encontro_id", ids).execute().data if ids else []
    por_enc = {}
    for r in resp:
        por_enc.setdefault(r["encontro_id"], {})[r["membro_id"]] = r["posso"]

    lista = []
    for e in encs:
        respostas = por_enc.get(e["id"], {})
        podem = [nomes[m] for m, v in respostas.items() if v and m in nomes]
        nao = [nomes[m] for m, v in respostas.items() if not v and m in nomes]
        lista.append({
            "id": e["id"],
            "inicio": e["inicio"],
            "por": nomes.get(e["proposto_por"], ""),
            "meu": e["proposto_por"] == session["membro_id"],
            "confirmado": bool(e["confirmado"]),
            "link": e.get("link") or "",
            "podem": podem,
            "nao_podem": nao,
            "sem_resposta": max(total - len(podem) - len(nao), 0),
            "minha": respostas.get(session["membro_id"]),
        })

    candidatas = [x for x in lista if x["podem"]]
    melhor = None
    if candidatas:
        topo = max(len(x["podem"]) for x in candidatas)
        melhor = next(x["id"] for x in candidatas if len(x["podem"]) == topo)

    return {
        "livro": {"titulo": livro["titulo"]},
        "terminaram": len(terminaram),
        "total": total,
        "eu_terminei": session["membro_id"] in terminaram,
        "encontros": lista,
        "melhor": melhor,
        "admin": eh_admin(),
    }


@app.route("/encontro/propor", methods=["POST"])
def propor_encontro():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    livro = livro_do_mes()
    if not livro:
        return {"erro": "Ainda não há livro do mês."}, 400
    d = request.get_json(silent=True) or {}
    try:
        inicio = ler_data_utc(d.get("inicio", ""))
    except ValueError:
        return {"erro": "Escolha um dia e um horário válidos."}, 400
    if inicio <= agora_utc():
        return {"erro": "Escolha um horário no futuro."}, 400
    if inicio > agora_utc() + timedelta(days=120):
        return {"erro": "Escolha uma data dentro dos próximos 4 meses."}, 400

    abertas = (
        supabase.table("encontros").select("id")
        .eq("livro_id", livro["id"]).eq("proposto_por", session["membro_id"])
        .gte("inicio", iso_z(agora_utc())).execute().data
    )
    if len(abertas) >= 5:
        return {"erro": "Você já tem 5 horários propostos. Apague algum para sugerir outro."}, 400

    novo = supabase.table("encontros").insert({
        "livro_id": livro["id"],
        "inicio": iso_z(inicio),
        "proposto_por": session["membro_id"],
    }).execute().data[0]
    supabase.table("disponibilidade").upsert(
        {"encontro_id": novo["id"], "membro_id": session["membro_id"], "posso": True},
        on_conflict="encontro_id,membro_id",
    ).execute()
    return {"ok": True}


@app.route("/encontro/responder", methods=["POST"])
def responder_encontro():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    d = request.get_json(silent=True) or {}
    eid = d.get("encontro_id")
    posso = d.get("posso")
    if not isinstance(eid, int) or isinstance(eid, bool) or not isinstance(posso, bool):
        return {"erro": "Resposta inválida."}, 400
    if not supabase.table("encontros").select("id").eq("id", eid).execute().data:
        return {"erro": "Esse horário não existe mais."}, 404
    supabase.table("disponibilidade").upsert(
        {"encontro_id": eid, "membro_id": session["membro_id"], "posso": posso},
        on_conflict="encontro_id,membro_id",
    ).execute()
    return {"ok": True}


@app.route("/encontro/confirmar", methods=["POST"])
def confirmar_encontro():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    if not eh_admin():
        return {"erro": "Só quem organiza o clube pode confirmar o encontro."}, 403
    d = request.get_json(silent=True) or {}
    eid = d.get("encontro_id")
    if not isinstance(eid, int) or isinstance(eid, bool):
        return {"erro": "Horário inválido."}, 400
    link = str(d.get("link", "")).strip()[:500]
    if link and not link.startswith("https://"):
        return {"erro": "O link precisa começar com https://"}, 400
    achado = supabase.table("encontros").select("livro_id").eq("id", eid).execute().data
    if not achado:
        return {"erro": "Esse horário não existe mais."}, 404
    supabase.table("encontros").update({"confirmado": False, "link": None}) \
        .eq("livro_id", achado[0]["livro_id"]).eq("confirmado", True).execute()
    supabase.table("encontros").update({"confirmado": True, "link": link or None}).eq("id", eid).execute()
    return {"ok": True}


@app.route("/encontro/desmarcar", methods=["POST"])
def desmarcar_encontro():
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    if not eh_admin():
        return {"erro": "Só quem organiza o clube pode desmarcar o encontro."}, 403
    d = request.get_json(silent=True) or {}
    eid = d.get("encontro_id")
    if not isinstance(eid, int) or isinstance(eid, bool):
        return {"erro": "Horário inválido."}, 400
    supabase.table("encontros").update({"confirmado": False, "link": None}).eq("id", eid).execute()
    return {"ok": True}


@app.route("/encontro/<int:encontro_id>", methods=["DELETE"])
def apagar_encontro(encontro_id):
    if "membro_id" not in session:
        return {"erro": "Entre no clube primeiro."}, 401
    achado = supabase.table("encontros").select("proposto_por").eq("id", encontro_id).execute().data
    if not achado:
        return {"ok": True}
    if achado[0]["proposto_por"] != session["membro_id"] and not eh_admin():
        return {"erro": "Só quem propôs (ou a organizadora) pode apagar."}, 403
    supabase.table("encontros").delete().eq("id", encontro_id).execute()
    return {"ok": True}

@app.route("/sw.js")
def service_worker():
    resposta = send_from_directory("static", "sw.js")
    resposta.headers["Cache-Control"] = "no-cache"
    resposta.headers["Content-Type"] = "application/javascript"
    return resposta


if __name__ == "__main__":
    app.run(debug=True)