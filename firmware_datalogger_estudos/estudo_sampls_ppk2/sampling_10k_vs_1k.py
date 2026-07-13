"""
Análise de Consumo Energético — Nordic PPK2
============================================
Compara 1k sps vs 10k sps e determina qual a taxa adequada
para estudos de energia/bateria com dispositivos IoT/LoRa.

Utilização:
    python analise_consumo_ppk2_1k_10k.py

Ficheiros esperados (configuráveis em CONFIG abaixo):
    consumos_10min_1ksamp.csv
    consumos_10min_10ksamp.csv

Formato esperado dos CSV (exportação padrão do nRF Power Profiler):
    Timestamp(ms), Current(uA), D0-D7

Nota sobre o limite do Excel:
    A 10k sps, 10 minutos geram 6 000 000 linhas — acima do limite do
    Excel (1 048 576). Usar sempre o CSV original exportado pelo
    nRF Power Profiler, sem abrir em Excel.
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import os
import warnings
warnings.filterwarnings("ignore")

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

CONFIG = {
    "ficheiros": {
        "1k":  "consumos_10min_1ksamp.csv",
        "10k": "consumos_10min_10ksamp.csv",
    },
    # Threshold para deteção de picos de alta corrente (µA)
    "peak_threshold_uA": 5000,       
    "min_samples_per_peak": 10,
    # Tensão de operação (para cálculo de potência/energia em mW·h)
    "vdd_V": 3.3,
    # Pasta de output para figuras
    "output_dir": ".",
}

CORES = {
    "1k":   "#3266ad",
    "10k":  "#d85a30",
    "100k": "#3a9e5f",
}

# =============================================================================
# 1. CARREGAMENTO E VALIDAÇÃO DOS DADOS
# =============================================================================

def carregar_ficheiro(path: str, label: str) -> pd.DataFrame | None:
    """Carrega CSV do PPK2 e normaliza colunas."""
    if not os.path.exists(path):
        print(f"  [AVISO] Ficheiro não encontrado: {path}")
        return None
    df = pd.read_csv(path, low_memory=False)
    df["Timestamp(ms)"] = pd.to_numeric(df["Timestamp(ms)"], errors="coerce")
    df["Current(uA)"]   = pd.to_numeric(df["Current(uA)"],   errors="coerce")
    df = df.dropna(subset=["Timestamp(ms)", "Current(uA)"]).reset_index(drop=True)
    duracao_s = (df["Timestamp(ms)"].max() - df["Timestamp(ms)"].min()) / 1000.0
    sps_real  = len(df) / duracao_s if duracao_s > 0 else 0
    print(f"  [{label}] {len(df):>9,} amostras | {duracao_s:.1f} s | {sps_real:,.0f} sps reais")
    return df


def carregar_todos(config: dict) -> dict:
    print("\n" + "="*60)
    print("1. CARREGAMENTO DOS DADOS")
    print("="*60)
    dados = {}
    for label, fname in config["ficheiros"].items():
        df = carregar_ficheiro(fname, label)
        if df is not None:
            dados[label] = df
    return dados

# =============================================================================
# 2. MÉTRICAS ENERGÉTICAS GLOBAIS
# =============================================================================

def calcular_metricas(df: pd.DataFrame, vdd: float) -> dict:
    """Calcula corrente média, carga e energia por integração trapezoidal."""
    t = df["Timestamp(ms)"].values
    c = df["Current(uA)"].values
    duracao_s = (t.max() - t.min()) / 1000.0

    # Carga total (integral trapezoidal): µA·ms → µC  (dividir por 1000)
    carga_uC   = np.trapezoid(c, x=t) / 1000.0
    media_uA   = carga_uC / duracao_s          # µA (= µC/s)
    energia_uJ = carga_uC * vdd                # µC × V = µJ
    mAh        = (media_uA / 1000.0) * (duracao_s / 3600.0)

    return {
        "duracao_s":  duracao_s,
        "media_uA":   media_uA,
        "std_uA":     np.std(c),
        "min_uA":     c.min(),
        "max_uA":     c.max(),
        "p50_uA":     np.percentile(c, 50),
        "p95_uA":     np.percentile(c, 95),
        "p99_uA":     np.percentile(c, 99),
        "carga_uC":   carga_uC,
        "energia_uJ": energia_uJ,
        "mAh":        mAh,
    }


def tabela_metricas(dados: dict, config: dict):
    print("\n" + "="*60)
    print("2. MÉTRICAS ENERGÉTICAS GLOBAIS")
    print("="*60)
    resultados = {}
    for label, df in dados.items():
        m = calcular_metricas(df, config["vdd_V"])
        resultados[label] = m
        print(f"\n  [{label} sps]  duração={m['duracao_s']:.1f}s")
        print(f"    Corrente média (trapz) : {m['media_uA']:.4f} µA")
        print(f"    Desvio padrão          : {m['std_uA']:.3f} µA")
        print(f"    Min / Max              : {m['min_uA']:.2f} / {m['max_uA']:.2f} µA")
        print(f"    Percentis p50/p95/p99  : {m['p50_uA']:.1f} / {m['p95_uA']:.1f} / {m['p99_uA']:.1f} µA")
        print(f"    Carga total            : {m['carga_uC']:.2f} µC")
        print(f"    Energia total          : {m['energia_uJ']:.2f} µJ  ({m['energia_uJ']/3600:.4f} µWh)")
        print(f"    Consumo (@ {config['vdd_V']}V)       : {m['mAh']*1000:.4f} µAh  ({m['mAh']*1000:.6f} mAh)")
    return resultados


def projecao_2h(resultados: dict, config: dict):
    print("\n" + "="*60)
    print("3. PROJEÇÃO PARA 2 HORAS")
    print("="*60)
    print(f"  (assume regime estacionário — extrapolação linear)")
    print(f"  {'Taxa':>8} | {'Média (µA)':>12} | {'Carga 2h (mC)':>14} | {'Consumo 2h (mAh)':>17}")
    print(f"  {'-'*8}-+-{'-'*12}-+-{'-'*14}-+-{'-'*17}")
    for label, m in resultados.items():
        carga_2h_mC  = m["media_uA"] * 7200 / 1000.0
        consumo_2h   = (m["media_uA"] / 1000.0) * 2.0    # mAh
        print(f"  {label+' sps':>8} | {m['media_uA']:>12.4f} | {carga_2h_mC:>14.4f} | {consumo_2h:>17.6f}")

# =============================================================================
# 3. ANÁLISE DE PICOS
# =============================================================================

def detetar_picos(df: pd.DataFrame, threshold_uA: float) -> list:
    """Deteta grupos contíguos de amostras acima do threshold."""
    c = df["Current(uA)"].values
    t = df["Timestamp(ms)"].values
    picos = []
    in_peak, start_i = False, 0
    for i, v in enumerate(c):
        if v > threshold_uA and not in_peak:
            in_peak = True
            start_i = i
        elif v <= threshold_uA and in_peak:
            in_peak = False
            seg_c = c[start_i:i]
            seg_t = t[start_i:i]
            dur_ms   = seg_t[-1] - seg_t[0] if len(seg_t) > 1 else 0.0
            carga_uC = np.trapezoid(seg_c, x=seg_t) / 1000.0 if len(seg_t) > 1 else 0.0
            picos.append({
                "t_start_ms": seg_t[0],
                "dur_ms":     dur_ms,
                "max_uA":     seg_c.max(),
                "n_amostras": i - start_i,
                "carga_uC":   carga_uC,
            })
    return picos


def analise_picos(dados: dict, config: dict):
    print("\n" + "="*60)
    print(f"4. ANÁLISE DE PICOS  (threshold = {config['peak_threshold_uA']/1000:.0f} mA)")
    print("="*60)
    thr = config["peak_threshold_uA"]
    picos_por_taxa = {}
    for label, df in dados.items():
        picos = detetar_picos(df, thr)
        picos_por_taxa[label] = picos
        if not picos:
            print(f"\n  [{label} sps] Nenhum pico detetado.")
            continue
        durs      = [p["dur_ms"]     for p in picos]
        n_samp    = [p["n_amostras"] for p in picos]
        cargas    = [p["carga_uC"]   for p in picos]
        energia_p = sum(cargas)
        t_total   = (df["Timestamp(ms)"].max() - df["Timestamp(ms)"].min()) / 1000.0
        carga_tot = np.trapezoid(df["Current(uA)"].values, x=df["Timestamp(ms)"].values) / 1000.0
        print(f"\n  [{label} sps]  {len(picos)} picos detetados")
        print(f"    Duração  — min={min(durs):.2f}ms  mediana={np.median(durs):.2f}ms  max={max(durs):.2f}ms")
        print(f"    Amostras/pico — min={min(n_samp)}  mediana={np.median(n_samp):.1f}  max={max(n_samp)}")
        print(f"    Energia nos picos: {energia_p:.3f} µC  ({100*energia_p/carga_tot:.2f}% do total)")

        # Aviso se picos com poucas amostras
        mal_resolvidos = [p for p in picos if p["n_amostras"] < config["min_samples_per_peak"]]
        if mal_resolvidos:
            print(f"    [!] {len(mal_resolvidos)} pico(s) com <{config['min_samples_per_peak']} amostras "
                  f"(resolução insuficiente para precisão)")
    return picos_por_taxa


def analise_resolucao_temporal(dados: dict, config: dict):
    """
    Simula o que diferentes taxas de amostragem 'veriam' no maior pico,
    usando o ficheiro de maior resolução disponível como referência.
    """
    print("\n" + "="*60)
    print("5. RESOLUÇÃO TEMPORAL — SIMULAÇÃO DE DOWNSAMPLING")
    print("="*60)

    # Usar o ficheiro com mais resolução disponível como referência
    ref_label = max(dados.keys(), key=lambda k: len(dados[k]))
    df_ref    = dados[ref_label]
    thr       = config["peak_threshold_uA"]

    picos_ref = detetar_picos(df_ref, thr)
    if not picos_ref:
        print("  Sem picos para analisar.")
        return {}

    # Maior pico por carga
    pico = max(picos_ref, key=lambda p: p["carga_uC"])
    t0, t1 = pico["t_start_ms"] - 5, pico["t_start_ms"] + pico["dur_ms"] + 5

    mask = (df_ref["Timestamp(ms)"] >= t0) & (df_ref["Timestamp(ms)"] <= t1)
    tc = df_ref["Timestamp(ms)"].values[mask]
    cc = df_ref["Current(uA)"].values[mask]
    charge_ref = np.trapezoid(cc, x=tc) / 1000.0

    print(f"\n  Referência: {ref_label} sps")
    print(f"  Pico analisado: t={pico['t_start_ms']:.1f}ms | dur={pico['dur_ms']:.1f}ms | "
          f"max={pico['max_uA']/1000:.2f}mA | carga={charge_ref:.4f}µC\n")

    # Determinar sps da referência
    dt_ms   = np.median(np.diff(tc))
    sps_ref = int(round(1000.0 / dt_ms)) if dt_ms > 0 else 1

    taxas_sim = {}
    header = f"  {'Taxa simulada':>16} | {'Max (mA)':>10} | {'Carga (µC)':>12} | {'Erro (%)':>10} | {'Amostras/pico':>14} | {'Adequado?':>10}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for sps_alvo in [100000, 50000, 10000, 5000, 2000, 1000, 500]:
        if sps_alvo > sps_ref:
            continue
        step = max(1, sps_ref // sps_alvo)
        idx  = np.arange(0, len(tc), step)
        tc_s, cc_s = tc[idx], cc[idx]
        charge_s   = np.trapezoid(cc_s, x=tc_s) / 1000.0
        erro       = abs(charge_s - charge_ref) / charge_ref * 100
        n_samp     = len(cc_s)
        adequado   = "✓" if n_samp >= config["min_samples_per_peak"] and erro < 5.0 else "✗"
        taxas_sim[sps_alvo] = {"max_mA": cc_s.max()/1000, "carga_uC": charge_s,
                                "erro_pct": erro, "n_amostras": n_samp, "adequado": adequado}
        print(f"  {str(sps_alvo)+' sps':>16} | {cc_s.max()/1000:>10.2f} | {charge_s:>12.4f} | "
              f"{erro:>10.2f} | {n_samp:>14} | {adequado:>10}")

    # Recomendação
    recomendados = [sps for sps, v in taxas_sim.items() if v["adequado"] == "✓"]
    if recomendados:
        melhor = max(recomendados)  # menor taxa que ainda é adequada = mais eficiente
        print(f"\n  → Taxa mínima recomendada para estudo energético: {melhor:,} sps")
        print(f"    (erro na energia do pico <5% e ≥{config['min_samples_per_peak']} amostras por pico)")
    return taxas_sim


def calcular_nyquist_picos(picos_por_taxa: dict):
    """Estima a frequência de corte necessária com base na duração dos picos."""
    print("\n" + "="*60)
    print("6. CRITÉRIO DE NYQUIST PARA TRANSIENTES")
    print("="*60)
    print("  Para transientes (picos), o critério relevante é a duração mínima,")
    print("  não a frequência do sinal. Regra prática: ≥10 amostras por pico.\n")

    for label, picos in picos_por_taxa.items():
        if not picos:
            continue
        durs = [p["dur_ms"] for p in picos if p["dur_ms"] > 0]
        if not durs:
            continue
        dur_min = min(durs)
        sps_necessario = (10.0 / dur_min) * 1000 if dur_min > 0 else float("inf")
        print(f"  [{label} sps]  pico mais curto = {dur_min:.2f} ms  →  "
              f"necessita ≥ {sps_necessario:,.0f} sps para 10 amostras/pico")

# =============================================================================
# 4. FIGURAS
# =============================================================================

def figura_overview(dados: dict, resultados: dict, config: dict):
    """Fig 1: Corrente ao longo do tempo para cada taxa."""
    fig, axes = plt.subplots(len(dados), 1, figsize=(14, 3.5 * len(dados)), sharex=False)
    if len(dados) == 1:
        axes = [axes]

    for ax, (label, df) in zip(axes, dados.items()):
        t = df["Timestamp(ms)"].values / 1000.0   # → segundos
        c = df["Current(uA)"].values / 1000.0      # → mA
        m = resultados[label]
        ax.plot(t, c, linewidth=0.4, alpha=0.7, color=CORES.get(label, "steelblue"), rasterized=True)
        ax.axhline(m["media_uA"] / 1000.0, color="black", linewidth=1.2,
                   linestyle="--", label=f"Média = {m['media_uA']:.2f} µA")
        ax.set_ylabel("Corrente (mA)", fontsize=10)
        ax.set_title(f"{label} sps — duração {m['duracao_s']:.0f}s", fontsize=11)
        ax.legend(fontsize=9, loc="upper right")
        ax.set_ylim(bottom=0)
        ax.grid(True, alpha=0.3)
        ax.set_xlabel("Tempo (s)", fontsize=10)

    fig.suptitle("Corrente ao longo do tempo — comparação de taxas de amostragem", fontsize=13, y=1.01)
    plt.tight_layout()
    out = os.path.join(config["output_dir"], "fig1_overview_corrente.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Guardada: {out}")


def figura_distribuicao(dados: dict, resultados: dict, config: dict):
    """Fig 2: Histogramas de corrente (escala log) e boxplot comparativo."""
    fig = plt.figure(figsize=(14, 5))
    gs  = gridspec.GridSpec(1, 2, width_ratios=[2, 1])
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1])

    for label, df in dados.items():
        c = df["Current(uA)"].values
        ax1.hist(c, bins=300, density=True, alpha=0.55,
                 color=CORES.get(label, "gray"), label=f"{label} sps", range=(0, 500))
    ax1.set_xlabel("Corrente (µA)", fontsize=10)
    ax1.set_ylabel("Densidade", fontsize=10)
    ax1.set_title("Distribuição de corrente (0–500 µA)", fontsize=11)
    ax1.legend(fontsize=9)
    ax1.grid(True, alpha=0.3)

    box_data  = [df["Current(uA)"].values[df["Current(uA)"].values < 500] for df in dados.values()]
    bp = ax2.boxplot(box_data, labels=list(dados.keys()), patch_artist=True, notch=False)
    for patch, label in zip(bp["boxes"], dados.keys()):
        patch.set_facecolor(CORES.get(label, "gray"))
        patch.set_alpha(0.6)
    ax2.set_ylabel("Corrente (µA)", fontsize=10)
    ax2.set_title("Boxplot (excl. picos >500µA)", fontsize=11)
    ax2.grid(True, alpha=0.3, axis="y")

    fig.suptitle("Distribuição de corrente por taxa de amostragem", fontsize=13)
    plt.tight_layout()
    out = os.path.join(config["output_dir"], "fig2_distribuicao.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Guardada: {out}")


def figura_pico_zoom(dados: dict, config: dict):
    """Fig 3: Zoom no maior pico — comparação visual da resolução."""
    # Encontrar o maior pico em qualquer ficheiro
    thr = config["peak_threshold_uA"]
    melhor_pico = None
    melhor_label = None
    for label, df in dados.items():
        picos = detetar_picos(df, thr)
        if picos:
            p = max(picos, key=lambda x: x["carga_uC"])
            if melhor_pico is None or p["carga_uC"] > melhor_pico["carga_uC"]:
                melhor_pico  = p
                melhor_label = label

    if melhor_pico is None:
        print("  [AVISO] Sem picos para zoom.")
        return

    t0 = melhor_pico["t_start_ms"] - 10
    t1 = melhor_pico["t_start_ms"] + melhor_pico["dur_ms"] + 10

    fig, ax = plt.subplots(figsize=(12, 5))
    for label, df in dados.items():
        mask = (df["Timestamp(ms)"] >= t0) & (df["Timestamp(ms)"] <= t1)
        sub  = df[mask]
        if sub.empty:
            continue
        lw   = 1.8 if label == melhor_label else 1.0
        ax.plot(sub["Timestamp(ms)"].values, sub["Current(uA)"].values / 1000.0,
                label=f"{label} sps ({len(sub)} amostras)",
                color=CORES.get(label, "gray"), linewidth=lw,
                marker="o" if len(sub) < 60 else None, markersize=3)

    ax.axhline(thr / 1000.0, color="red", linewidth=0.8, linestyle=":", alpha=0.6,
               label=f"Threshold {thr/1000:.0f} mA")
    ax.set_xlabel("Timestamp (ms)", fontsize=10)
    ax.set_ylabel("Corrente (mA)", fontsize=10)
    ax.set_title(f"Zoom no maior pico  ({melhor_pico['dur_ms']:.1f} ms, {melhor_pico['max_uA']/1000:.2f} mA max)",
                 fontsize=11)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    out = os.path.join(config["output_dir"], "fig3_zoom_pico.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Guardada: {out}")


def figura_erro_downsampling(dados: dict, config: dict):
    """Fig 4: Erro na energia do pico em função do sps simulado."""
    ref_label = max(dados.keys(), key=lambda k: len(dados[k]))
    df_ref    = dados[ref_label]
    thr       = config["peak_threshold_uA"]
    picos_ref = detetar_picos(df_ref, thr)
    if not picos_ref:
        return

    pico = max(picos_ref, key=lambda p: p["carga_uC"])
    t0   = pico["t_start_ms"] - 5
    t1   = pico["t_start_ms"] + pico["dur_ms"] + 5
    mask = (df_ref["Timestamp(ms)"] >= t0) & (df_ref["Timestamp(ms)"] <= t1)
    tc   = df_ref["Timestamp(ms)"].values[mask]
    cc   = df_ref["Current(uA)"].values[mask]
    charge_ref = np.trapezoid(cc, x=tc) / 1000.0

    dt_ms   = np.median(np.diff(tc))
    sps_ref = int(round(1000.0 / dt_ms)) if dt_ms > 0 else 1

    sps_list, erros, n_samps = [], [], []
    for sps_alvo in sorted([100000, 50000, 20000, 10000, 5000, 2000, 1000, 500, 200]):
        if sps_alvo > sps_ref:
            continue
        step    = max(1, sps_ref // sps_alvo)
        idx     = np.arange(0, len(tc), step)
        tc_s    = tc[idx]
        cc_s    = cc[idx]
        charge_s = np.trapezoid(cc_s, x=tc_s) / 1000.0
        erro     = abs(charge_s - charge_ref) / charge_ref * 100
        sps_list.append(sps_alvo)
        erros.append(erro)
        n_samps.append(len(cc_s))

    fig, ax1 = plt.subplots(figsize=(10, 5))
    ax2 = ax1.twinx()

    ax1.semilogx(sps_list, erros, "o-", color="#d85a30", linewidth=2, markersize=6, label="Erro energia (%)")
    ax1.axhline(5.0, color="#d85a30", linestyle="--", linewidth=0.8, alpha=0.5, label="Limite 5%")
    ax1.axhline(1.0, color="green",   linestyle="--", linewidth=0.8, alpha=0.5, label="Limite 1%")
    ax1.set_xlabel("Taxa de amostragem (sps)", fontsize=10)
    ax1.set_ylabel("Erro na energia do pico (%)", fontsize=10, color="#d85a30")
    ax1.tick_params(axis="y", labelcolor="#d85a30")
    ax1.set_ylim(bottom=0)

    ax2.semilogx(sps_list, n_samps, "s--", color="#3266ad", linewidth=1.5, markersize=5, label="Amostras/pico")
    ax2.axhline(config["min_samples_per_peak"], color="#3266ad", linestyle=":", linewidth=0.8,
                alpha=0.5, label=f"Mín. {config['min_samples_per_peak']} amostras")
    ax2.set_ylabel("Nº amostras no pico", fontsize=10, color="#3266ad")
    ax2.tick_params(axis="y", labelcolor="#3266ad")

    # Legenda combinada
    lines1, labs1 = ax1.get_legend_handles_labels()
    lines2, labs2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labs1 + labs2, fontsize=9, loc="upper right")

    ax1.set_title(f"Erro na energia vs taxa de amostragem\n(referência: {ref_label} sps, pico de {pico['dur_ms']:.1f}ms)",
                  fontsize=11)
    ax1.grid(True, alpha=0.3, which="both")
    plt.tight_layout()
    out = os.path.join(config["output_dir"], "fig4_erro_downsampling.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Guardada: {out}")


def figura_comparacao_energia(resultados: dict, config: dict):
    """Fig 5: Barras comparando corrente média e projeção 2h."""
    labels = list(resultados.keys())
    medias = [resultados[l]["media_uA"] for l in labels]
    mAh_2h = [(resultados[l]["media_uA"] / 1000.0) * 2.0 for l in labels]
    cores  = [CORES.get(l, "gray") for l in labels]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 5))

    bars1 = ax1.bar([l + " sps" for l in labels], medias, color=cores, alpha=0.8, edgecolor="white")
    for bar, val in zip(bars1, medias):
        ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1,
                 f"{val:.2f} µA", ha="center", va="bottom", fontsize=9)
    ax1.set_ylabel("Corrente média (µA)", fontsize=10)
    ax1.set_title("Corrente média por taxa", fontsize=11)
    ax1.grid(True, alpha=0.3, axis="y")
    delta = max(medias) - min(medias)
    ax1.set_ylim(min(medias) - delta * 2, max(medias) + delta * 5)

    bars2 = ax2.bar([l + " sps" for l in labels], mAh_2h, color=cores, alpha=0.8, edgecolor="white")
    for bar, val in zip(bars2, mAh_2h):
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + val*0.005,
                 f"{val*1000:.4f} µAh", ha="center", va="bottom", fontsize=9)
    ax2.set_ylabel("Consumo estimado (mAh)", fontsize=10)
    ax2.set_title("Projeção 2h de consumo", fontsize=11)
    ax2.grid(True, alpha=0.3, axis="y")
    ax2.set_ylim(0, max(mAh_2h) * 1.3)

    fig.suptitle("Comparação energética entre taxas de amostragem", fontsize=13)
    plt.tight_layout()
    out = os.path.join(config["output_dir"], "fig5_comparacao_energia.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Guardada: {out}")

# =============================================================================
# 5. COMPARAÇÃO DIRETA 1k vs 10k
# =============================================================================

def comparacao_direta(dados: dict, config: dict) -> dict:
    """
    Compara 1k vs 10k janela a janela (30s) e calcula:
    - Diferença de corrente média em cada janela
    - Erro relativo global
    - Quantos picos são detetados em cada taxa
    - Energia perdida nos picos ao usar 1k em vez de 10k
    """
    if "1k" not in dados or "10k" not in dados:
        return {}

    print("\n" + "="*60)
    print("7. COMPARAÇÃO DIRETA 1k sps vs 10k sps")
    print("="*60)

    df1  = dados["1k"]
    df10 = dados["10k"]
    thr  = config["peak_threshold_uA"]

    # --- Erro global na corrente média ---
    def avg_trap(df):
        t = df["Timestamp(ms)"].values
        c = df["Current(uA)"].values
        dur = (t.max() - t.min()) / 1000.0
        return np.trapezoid(c, x=t) / 1000.0 / dur

    avg1  = avg_trap(df1)
    avg10 = avg_trap(df10)
    erro_global = abs(avg1 - avg10) / avg10 * 100

    print(f"\n  Corrente média  — 1k: {avg1:.4f} µA  |  10k: {avg10:.4f} µA")
    print(f"  Erro relativo global: {erro_global:.4f}%  ({abs(avg1-avg10):.4f} µA de diferença)")

    # --- Análise por janelas de 30s ---
    janela_ms = 30_000
    t_max = min(df1["Timestamp(ms)"].max(), df10["Timestamp(ms)"].max())
    janelas = np.arange(0, t_max, janela_ms)

    erros_janela, avgs1, avgs10 = [], [], []
    for t0 in janelas:
        t1 = t0 + janela_ms
        sub1  = df1[(df1["Timestamp(ms)"] >= t0)  & (df1["Timestamp(ms)"] < t1)]
        sub10 = df10[(df10["Timestamp(ms)"] >= t0) & (df10["Timestamp(ms)"] < t1)]
        if sub1.empty or sub10.empty:
            continue
        a1  = avg_trap(sub1)
        a10 = avg_trap(sub10)
        avgs1.append(a1)
        avgs10.append(a10)
        erros_janela.append(abs(a1 - a10) / a10 * 100 if a10 > 0 else 0)

    print(f"\n  Análise por janelas de 30s ({len(erros_janela)} janelas):")
    print(f"    Erro médio    : {np.mean(erros_janela):.4f}%")
    print(f"    Erro máximo   : {np.max(erros_janela):.4f}%")
    print(f"    Erro mínimo   : {np.min(erros_janela):.4f}%")
    print(f"    Desvio padrão : {np.std(erros_janela):.4f}%")

    # --- Energia nos picos: o que o 1k perde vs 10k ---
    picos1  = detetar_picos(df1,  thr)
    picos10 = detetar_picos(df10, thr)

    e_picos1  = sum(p["carga_uC"] for p in picos1)
    e_picos10 = sum(p["carga_uC"] for p in picos10)
    e_total1  = np.trapezoid(df1["Current(uA)"].values,  x=df1["Timestamp(ms)"].values)  / 1000.0
    e_total10 = np.trapezoid(df10["Current(uA)"].values, x=df10["Timestamp(ms)"].values) / 1000.0

    print(f"\n  Picos >5mA detetados  — 1k: {len(picos1)}  |  10k: {len(picos10)}")
    print(f"  Energia nos picos     — 1k: {e_picos1:.3f} µC ({100*e_picos1/e_total1:.2f}% do total)")
    print(f"                          10k: {e_picos10:.3f} µC ({100*e_picos10/e_total10:.2f}% do total)")
    print(f"  Diferença de energia nos picos: {abs(e_picos1-e_picos10):.3f} µC "
          f"({abs(e_picos1-e_picos10)/e_picos10*100:.2f}% do valor 10k)")

    # --- Picos com boa resolução (≥10 amostras) ---
    bem_resolvidos1  = [p for p in picos1  if p["n_amostras"] >= config["min_samples_per_peak"]]
    bem_resolvidos10 = [p for p in picos10 if p["n_amostras"] >= config["min_samples_per_peak"]]
    print(f"\n  Picos bem resolvidos (≥{config['min_samples_per_peak']} amostras):")
    print(f"    1k sps : {len(bem_resolvidos1)}/{len(picos1)}")
    print(f"    10k sps: {len(bem_resolvidos10)}/{len(picos10)}")

    # --- Conclusão automática ---
    print(f"\n  {'─'*50}")
    print(f"  CONCLUSÃO:")
    if erro_global < 1.0:
        print(f"  A diferença de corrente média entre 1k e 10k sps é de")
        print(f"  {erro_global:.4f}% — abaixo de 1%, negligenciável para estudos de energia.")
    else:
        print(f"  A diferença de corrente média é de {erro_global:.2f}%.")

    if len(bem_resolvidos1) < len(picos1):
        n_mal = len(picos1) - len(bem_resolvidos1)
        print(f"  A 1k sps, {n_mal} pico(s) têm <{config['min_samples_per_peak']} amostras — a")
        print(f"  forma de onda dos picos LoRa não é bem capturada.")
        print(f"  Para energia global: 1k sps é aceitável.")
        print(f"  Para caracterizar picos individuais: usar 10k sps.")
    print(f"  {'─'*50}")

    return {
        "avgs1": avgs1, "avgs10": avgs10, "erros_janela": erros_janela,
        "janelas_s": [t/1000 for t in janelas[:len(erros_janela)]],
        "picos1": picos1, "picos10": picos10,
    }


def figura_comparacao_direta(comp: dict, config: dict):
    """Fig 6: 1k vs 10k — corrente por janela e erro relativo."""
    if not comp:
        return

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8), sharex=True)

    t_s = comp["janelas_s"]
    ax1.plot(t_s, comp["avgs1"],  "o-", color=CORES["1k"],  linewidth=1.5,
             markersize=4, label="1k sps")
    ax1.plot(t_s, comp["avgs10"], "s-", color=CORES["10k"], linewidth=1.5,
             markersize=4, label="10k sps")
    ax1.set_ylabel("Corrente média (µA)", fontsize=10)
    ax1.set_title("Corrente média por janela de 30s — 1k vs 10k sps", fontsize=11)
    ax1.legend(fontsize=9)
    ax1.grid(True, alpha=0.3)

    ax2.bar(t_s, comp["erros_janela"], width=25, color="#888", alpha=0.7, label="Erro relativo (%)")
    ax2.axhline(1.0, color="red",   linewidth=1, linestyle="--", alpha=0.7, label="Limite 1%")
    ax2.axhline(0.5, color="green", linewidth=1, linestyle="--", alpha=0.7, label="Limite 0.5%")
    ax2.set_xlabel("Tempo (s)", fontsize=10)
    ax2.set_ylabel("Erro relativo (%)", fontsize=10)
    ax2.set_title("Erro relativo da corrente média (1k vs 10k) por janela de 30s", fontsize=11)
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    out = os.path.join(config["output_dir"], "fig6_comparacao_1k_vs_10k.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Guardada: {out}")


def figura_picos_comparacao(comp: dict, dados: dict, config: dict):
    """Fig 7: Scatter de todos os picos detetados por cada taxa ao longo do tempo."""
    if not comp:
        return

    fig, ax = plt.subplots(figsize=(13, 5))

    for label, picos in [("1k sps", comp["picos1"]), ("10k sps", comp["picos10"])]:
        if not picos:
            continue
        ts   = [p["t_start_ms"] / 1000 for p in picos]
        maxs = [p["max_uA"] / 1000 for p in picos]
        ax.scatter(ts, maxs, label=label, color=CORES.get(label.split()[0], "gray"),
                   alpha=0.7, s=30, zorder=3)

    ax.axhline(config["peak_threshold_uA"] / 1000, color="red", linewidth=0.8,
               linestyle=":", alpha=0.6, label=f"Threshold {config['peak_threshold_uA']/1000:.0f} mA")
    ax.set_xlabel("Tempo (s)", fontsize=10)
    ax.set_ylabel("Corrente máxima do pico (mA)", fontsize=10)
    ax.set_title("Picos >5mA detetados ao longo do tempo — 1k vs 10k sps", fontsize=11)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    out = os.path.join(config["output_dir"], "fig7_picos_timeline.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Guardada: {out}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("\n" + "="*60)
    print("  ANÁLISE DE CONSUMO ENERGÉTICO — Nordic PPK2")
    print("  1k sps vs 10k sps")
    print("="*60)

    dados = carregar_todos(CONFIG)
    if not dados:
        print("\n[ERRO] Nenhum ficheiro carregado. Verifique os caminhos em CONFIG.")
        return

    resultados     = tabela_metricas(dados, CONFIG)
    projecao_2h(resultados, CONFIG)
    picos_por_taxa = analise_picos(dados, CONFIG)
    calcular_nyquist_picos(picos_por_taxa)
    analise_resolucao_temporal(dados, CONFIG)
    comp           = comparacao_direta(dados, CONFIG)

    print("\n" + "="*60)
    print("8. FIGURAS")
    print("="*60)
    figura_overview(dados, resultados, CONFIG)
    figura_distribuicao(dados, resultados, CONFIG)
    figura_pico_zoom(dados, CONFIG)

    print("\n" + "="*60)
    print("  CONCLUÍDO")
    print("="*60)


if __name__ == "__main__":
    main()