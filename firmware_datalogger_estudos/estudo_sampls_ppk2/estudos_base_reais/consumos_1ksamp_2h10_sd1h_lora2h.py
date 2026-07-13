"""
Análise de Consumo Energético Real — Nordic PPK2 (Captura Total)
=============================================================
Calcula o consumo total de energia medido num período real de operação
do dispositivo, integrando TODA a corrente sem descartar picos curtos.

Eventos contabilizados:
  - 100% da corrente integrada continuamente (Sem filtragem por limiar).

Utilização:
    python analise_consumo_real_2h.py
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import os
import warnings
warnings.filterwarnings("ignore")

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

CONFIG = {
    "ficheiro": r"C:\Nordic_dev\Programas\datalogger_pulsoshardware\estudo_sampls_ppk2\estudos_base_reais\consumos_1ksamp_2h10_sd1h_lora_2h_prototipo.csv",
    "ignorar_inicio_ms":     2000,      # Ignorar primeiros N ms (inicialização do hardware)
    "vdd_V":                 3.3,       # Tensão de operação
    "fator_seguranca":       0.30,      # 30% (degradação + autodescarga)
    "baterias_mAh":          [1000, 1500, 1700, 2000, 3000],  # Baterias a comparar
    "output_dir":            ".",
}

# =============================================================================
# 1. CARREGAMENTO
# =============================================================================

def carregar(cfg):
    path = cfg["ficheiro"]
    if not os.path.exists(path):
        print(f"[ERRO] Ficheiro não encontrado: {path}")
        return None

    print(f"  A carregar: {path}")
    df = pd.read_csv(path, low_memory=False)
    df["Timestamp(ms)"] = pd.to_numeric(df["Timestamp(ms)"], errors="coerce")
    df["Current(uA)"]   = pd.to_numeric(df["Current(uA)"],   errors="coerce")
    df = df.dropna(subset=["Timestamp(ms)", "Current(uA)"]).reset_index(drop=True)

    dur = (df["Timestamp(ms)"].max() - df["Timestamp(ms)"].min()) / 1000
    sps = len(df) / dur if dur > 0 else 0
    print(f"  {len(df):,} Amostras | {dur:.1f}s ({dur/60:.2f} min) | {sps:,.0f} sps")
    return df

# =============================================================================
# 2. CÁLCULO DO CONSUMO REAL (INTEGRAÇÃO TOTAL)
# =============================================================================

def calcular_consumo(df, cfg):
    t_raw = df["Timestamp(ms)"].values
    c_raw = df["Current(uA)"].values

    # Excluir apenas a inicialização do boot se configurado, o resto é mantido
    mask = t_raw >= cfg["ignorar_inicio_ms"]
    t = t_raw[mask]
    c = c_raw[mask]

    dur_s        = (t.max() - t.min()) / 1000
    carga_uC     = np.trapezoid(c, x=t) / 1000  # Integração contínua de todas as amostras
    avg_uA       = carga_uC / dur_s
    energia_uJ   = carga_uC * cfg["vdd_V"]
    mAh_periodo  = (avg_uA / 1000) * (dur_s / 3600)

    return {
        "t":           t,
        "c":           c,
        "t_raw":       t_raw,
        "c_raw":       c_raw,
        "dur_s":       dur_s,
        "carga_uC":    carga_uC,
        "avg_uA":      avg_uA,
        "energia_uJ":  energia_uJ,
        "mAh_periodo": mAh_periodo,
        "avg_mA":      avg_uA / 1000,
    }


def imprimir_consumo(r, cfg):
    print(f"\n  Período analisado:     {r['dur_s']:.1f}s  ({r['dur_s']/60:.2f} min  /  {r['dur_s']/3600:.4f}h)")
    print(f"  (Excluídos primeiros {cfg['ignorar_inicio_ms']}ms de boot do sistema)\n")
    print(f"  Carga total calculada: {r['carga_uC']:.2f} µC  =  {r['carga_uC']/1000:.4f} mC")
    print(f"  Corrente média global: {r['avg_uA']:.4f} µA")
    print(f"  Energia total:         {r['energia_uJ']:.2f} µJ  =  {r['energia_uJ']/1000:.4f} mJ")
    print(f"  Consumo no período:    {r['mAh_periodo']*1000:.4f} µAh  =  {r['mAh_periodo']:.6f} mAh")

# =============================================================================
# 3. PROJEÇÃO E AUTONOMIA
# =============================================================================

def calcular_projecao(r, cfg):
    avg_mA = r["avg_mA"]
    fs     = cfg["fator_seguranca"]

    proj = {
        "mAh_dia":  avg_mA * 24,
        "mAh_ano":  avg_mA * 24 * 365,
        "baterias": [],
    }

    for bat in cfg["baterias_mAh"]:
        auto_h    = bat / avg_mA
        auto_h_fs = auto_h * (1 - fs)
        proj["baterias"].append({
            "bat_mAh":    bat,
            "auto_h":     auto_h,
            "auto_h_fs":  auto_h_fs,
            "auto_anos":  auto_h / 8760,
            "auto_anos_fs": auto_h_fs / 8760,
        })
    return proj


def imprimir_projecao(r, proj, cfg):
    print(f"\n  Corrente média:        {r['avg_uA']:.4f} µA = {r['avg_mA']:.6f} mA")
    print(f"  Consumo/dia:           {proj['mAh_dia']*1000:.4f} µAh = {proj['mAh_dia']:.6f} mAh")
    print(f"  Consumo/ano:           {proj['mAh_ano']:.4f} mAh")

    print(f"\n  {'Bateria':>10} | {'Teórica':>12} | {'Com seg. {:.0f}%'.format(cfg['fator_seguranca']*100):>14}")
    print(f"  {'-'*10}-+-{'-'*12}-+-{'-'*14}")
    for b in proj["baterias"]:
        print(f"  {str(b['bat_mAh'])+' mAh':>10} | "
              f"{b['auto_anos']:.1f} anos ({b['auto_h']:.0f}h) | "
              f"{b['auto_anos_fs']:.1f} anos ({b['auto_h_fs']:.0f}h)")

# =============================================================================
# 4. ESTATÍSTICAS DOS EVENTOS (LIMIAR REBAIXADO PARA REGISTO COMPLETO)
# =============================================================================

def analisar_eventos(r, cfg):
    t, c = r["t"], r["c"]
    
    # Limiar em 0.1 µA garante que nada é ignorado na análise temporal de atividade
    THRESHOLD = 0.1   # µA
    GAP_MS    = 10    # ms rápido para apanhar spikes curtos

    eventos = []
    in_ev, start_i, last_above = False, 0, -1
    for i, v in enumerate(c):
        if v > THRESHOLD:
            if not in_ev:
                in_ev = True
                start_i = i
            last_above = i
        elif in_ev and (t[i] - t[last_above]) > GAP_MS:
            in_ev = False
            seg_c = c[start_i:last_above+1]
            seg_t = t[start_i:last_above+1]
            dur   = seg_t[-1] - seg_t[0]
            carga = np.trapezoid(seg_c, x=seg_t) / 1000
            if dur > 0:  # Regista absolutamente tudo, incluindo micro-picos
                eventos.append({
                    "t_start_s": seg_t[0] / 1000,
                    "dur_ms":     dur,
                    "max_uA":     seg_c.max(),
                    "carga_uC":   carga,
                })

    avg_base = c.mean()
    dur_base = r["dur_s"]

    return {
        "eventos":   eventos,
        "avg_base":  avg_base,
        "dur_base":  dur_base,
        "pct_base":  100.0,
    }


def imprimir_eventos(ev):
    print(f"\n  Análise de transições ativa.")
    print(f"  Amostras registadas no histórico contínuo: {len(ev['eventos'])}")

# =============================================================================
# 5. FIGURAS
# =============================================================================

def figura_perfil_completo(r, ev, cfg):
    """Fig 1: Perfil de corrente completo com linha de base."""
    t_s = r["t_raw"] / 1000
    c_mA = r["c_raw"] / 1000

    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(t_s, c_mA, linewidth=0.4, color="#3266ad", alpha=0.8,
            rasterized=True, label="Corrente medida")
    ax.axhline(r["avg_uA"] / 1000, color="green", linewidth=1.2,
               linestyle="--", label=f"Média Global = {r['avg_uA']:.2f} µA")
    ax.axvspan(0, cfg["ignorar_inicio_ms"]/1000, alpha=0.2, color="gray",
               label=f"Inicialização (excluída, {cfg['ignorar_inicio_ms']}ms)")

    ax.legend(fontsize=9)
    ax.set_xlabel("Tempo (s)", fontsize=10)
    ax.set_ylabel("Corrente (mA)", fontsize=10)
    ax.set_title("Perfil de corrente contínuo — Registo total sem descarte", fontsize=11)
    ax.grid(True, alpha=0.3)
    ax.set_ylim(bottom=0)
    plt.tight_layout()
    out = os.path.join(cfg["output_dir"], "real_fig1_perfil_completo.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Guardada: {out}")


def figura_autonomia(proj, cfg):
    """Fig 2: Autonomia por capacidade de bateria."""
    bats      = [b["bat_mAh"] for b in proj["baterias"]]
    anos_teo  = [b["auto_anos"] for b in proj["baterias"]]
    anos_fs   = [b["auto_anos_fs"] for b in proj["baterias"]]

    x     = np.arange(len(bats))
    width = 0.35

    fig, ax = plt.subplots(figsize=(10, 5))
    bars1 = ax.bar(x - width/2, anos_teo, width, label="Teórica",
                   color="#3266ad", alpha=0.8, edgecolor="white")
    bars2 = ax.bar(x + width/2, anos_fs,  width,
                   label=f"Com fator segurança {int(cfg['fator_seguranca']*100)}%",
                   color="#d85a30", alpha=0.8, edgecolor="white")

    for bar, v in zip(bars1, anos_teo):
        ax.text(bar.get_x() + bar.get_width()/2,
                bar.get_height() + 0.2,
                f"{v:.1f}a", ha="center", va="bottom", fontsize=9)
    for bar, v in zip(bars2, anos_fs):
        ax.text(bar.get_x() + bar.get_width()/2,
                bar.get_height() + 0.2,
                f"{v:.1f}a", ha="center", va="bottom", fontsize=9)

    ax.set_xticks(x)
    ax.set_xticklabels([f"{b} mAh" for b in bats], fontsize=10)
    ax.set_ylabel("Autonomia (anos)", fontsize=10)
    ax.set_title(f"Autonomia estimada por capacidade de bateria\n"
                 f"Corrente média real: {proj['mAh_dia']*1000/24:.2f} µA  |  "
                 f"Consumo/dia: {proj['mAh_dia']*1000:.2f} µAh", fontsize=11)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3, axis="y")
    plt.tight_layout()
    out = os.path.join(cfg["output_dir"], "real_fig2_autonomia.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Guardada: {out}")


def figura_distribuicao(r, cfg):
    """Fig 3: Histograma global da distribuição de corrente."""
    c = r["c"]
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Histograma completo de densidade
    ax1.hist(c, bins=200, density=True, color="#3266ad", alpha=0.7)
    ax1.axvline(r["avg_uA"], color="red", linewidth=1.5,
                linestyle="--", label=f"Média Global = {r['avg_uA']:.2f} µA")
    ax1.set_xlabel("Corrente (µA)", fontsize=10)
    ax1.set_ylabel("Densidade", fontsize=10)
    ax1.set_title("Distribuição completa de corrente", fontsize=11)
    ax1.legend(fontsize=9)
    ax1.grid(True, alpha=0.3)

    # Percentis
    percentis = [50, 75, 90, 95, 99, 99.9]
    vals = [np.percentile(c, p) for p in percentis]
    ax2.barh([f"P{p}" for p in percentis], vals, color="#3a9e5f", alpha=0.8)
    for i, v in enumerate(vals):
        ax2.text(v + 5, i, f"{v:.1f} µA", va="center", fontsize=9)
    ax2.set_xlabel("Corrente (µA)", fontsize=10)
    ax2.set_title("Percentis de corrente globais", fontsize=11)
    ax2.grid(True, alpha=0.3, axis="x")

    fig.suptitle("Análise estatística sem descarte de amostras", fontsize=12)
    plt.tight_layout()
    out = os.path.join(cfg["output_dir"], "real_fig3_distribuicao.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Guardada: {out}")


def figura_carga_acumulada(r, cfg):
    """Fig 4: Carga acumulada ao longo do tempo."""
    t_s  = r["t"] / 1000
    c    = r["c"]
    dt   = np.gradient(r["t"])
    carga_cum = np.cumsum(c * dt) / 1000   # µC

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(t_s, carga_cum, color="#3266ad", linewidth=1.2)
    ax.set_xlabel("Tempo (s)", fontsize=10)
    ax.set_ylabel("Carga acumulada (µC)", fontsize=10)
    ax.set_title(f"Carga acumulada contínua ao longo do período\n"
                 f"Total: {r['carga_uC']:.2f} µC = {r['carga_uC']/1000:.4f} mC  |  "
                 f"Energia: {r['energia_uJ']/1000:.3f} mJ", fontsize=11)
    ax.grid(True, alpha=0.3)

    ax.annotate(f"{r['carga_uC']:.1f} µC",
                xy=(t_s[-1], carga_cum[-1]),
                xytext=(t_s[-1]*0.85, carga_cum[-1]*0.9),
                arrowprops=dict(arrowstyle="->", color="red"),
                fontsize=10, color="red")

    plt.tight_layout()
    out = os.path.join(cfg["output_dir"], "real_fig4_carga_acumulada.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Guardada: {out}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("\n" + "="*60)
    print("  ANÁLISE DE CONSUMO ENERGÉTICO REAL (INTEGRAÇÃO TOTAL) — Nordic PPK2")
    print("="*60)

    print("\n" + "="*60)
    print("1. CARREGAMENTO")
    print("="*60)
    df = carregar(CONFIG)
    if df is None:
        return

    print("\n" + "="*60)
    print("2. CONSUMO TOTAL CONTÍNUO")
    print("="*60)
    resultado = calcular_consumo(df, CONFIG)
    imprimir_consumo(resultado, CONFIG)

    print("\n" + "="*60)
    print("3. EVENTOS DETETADOS")
    print("="*60)
    eventos = analisar_eventos(resultado, CONFIG)
    imprimir_eventos(eventos)

    print("\n" + "="*60)
    print("4. PROJEÇÃO E AUTONOMIA")
    print("="*60)
    projecao = calcular_projecao(resultado, CONFIG)
    imprimir_projecao(resultado, projecao, CONFIG)

    print("\n" + "="*60)
    print("5. FIGURAS")
    print("="*60)
    figura_perfil_completo(resultado, eventos, CONFIG)
    figura_autonomia(projecao, CONFIG)
    figura_distribuicao(resultado, CONFIG)
    figura_carga_acumulada(resultado, CONFIG)

    print("\n" + "="*60)
    print("  CONCLUÍDO SEGURO")
    print("="*60)


if __name__ == "__main__":
    main()