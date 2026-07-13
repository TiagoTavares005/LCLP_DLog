"""
Projeção Energética para 2 Horas — Nordic PPK2
!!!!!!!!!!!!!!!!!!!!!!!!ATENCAO ESTE FICHEIRO É UM EXEMPLO DE MODELO ENERGÉTICO SIMPLIFICADO PARA PROJEÇÃO DE CONSUMO EM 2 HORAS, BASEADO EM COMPONENTES EXTRAÍDOS DE DADOS REAIS. OS VALORES E ASSUNÇÕES SÃO APENAS ILUSTRATIVOS E DEVEM SER AJUSTADOS COM BASE EM MEDIÇÕES ESPECÍFICAS DO DISPOSITIVO E DO PERFIL DE USO PRETENDIDO.
================================================
Modelo baseado em 3 componentes extraídos dos dados reais (10k sps):

  1. Corrente de base  — sleep/idle entre eventos
  2. Evento de arranque — único, inclui join TTN + init SD
  3. Ciclo SD+LoRa — conservador (evento combinado usado como limite máximo
                     tanto para o ciclo SD isolado à 1h como para o ciclo
                     SD+LoRa às 2h)

Configuração do dispositivo (produção):
  - Aquisição:   5 em 5 minutos
  - Escrita SD:  1 em 1 hora  → 2 eventos em 2h
  - Envio LoRa:  2 em 2 horas → 1 evento em 2h
  (O ciclo SD das 1h e o ciclo SD+LoRa das 2h usam o mesmo custo
   medido — opção conservadora)

Utilização:
    python projecao_energia_2h.py

Ficheiro esperado:
    consumos_10min_10ksamp.csv  (exportação PPK2, 10k sps)
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
    "ficheiro":          "consumos_10min_10ksamp.csv",
    "vdd_V":             3.3,
    "capacidade_bat_mAh": 4400,
    "fator_seguranca":   0.30,       # 30% (degradação + autodescarga)
    "output_dir":        ".",

    # Janelas de extração (ms) — ajustar se necessário
    "janela_arranque_ms":  (0,    1000),
    "janela_base_inicio_ms": 10000,   # Ignorar os primeiros 10s (transitório arranque)
    "janela_evento_ms":    (295000, 325000),  # Ciclo SD+LoRa aos 5 min

    # Configuração real do dispositivo em produção
    "intervalo_sd_h":    1,    # Escrita SD de hora a hora
    "intervalo_lora_h":  2,    # LoRa de 2 em 2 horas
    "duracao_estudo_h":  2,    # Janela de projeção
}

# =============================================================================
# 1. CARREGAMENTO
# =============================================================================

def carregar(path):
    print(f"  A carregar: {path}")
    df = pd.read_csv(path, low_memory=False)
    df["Timestamp(ms)"] = pd.to_numeric(df["Timestamp(ms)"], errors="coerce")
    df["Current(uA)"]   = pd.to_numeric(df["Current(uA)"],   errors="coerce")
    df = df.dropna(subset=["Timestamp(ms)", "Current(uA)"]).reset_index(drop=True)
    dur = (df["Timestamp(ms)"].max() - df["Timestamp(ms)"].min()) / 1000
    print(f"  {len(df):,} amostras | {dur:.1f}s | {len(df)/dur:,.0f} sps")
    return df

# =============================================================================
# 2. EXTRAÇÃO DOS COMPONENTES
# =============================================================================

def extrair_componentes(df, cfg):
    t = df["Timestamp(ms)"].values
    c = df["Current(uA)"].values

    # --- Base ---
    t0_ev, t1_ev = cfg["janela_evento_ms"]
    mask_base = (t > cfg["janela_base_inicio_ms"]) & ~((t >= t0_ev) & (t <= t1_ev))
    avg_base_uA = np.mean(c[mask_base])

    # --- Arranque ---
    t0_a, t1_a = cfg["janela_arranque_ms"]
    mask_arr = (t >= t0_a) & (t <= t1_a)
    t_a, c_a = t[mask_arr], c[mask_arr]
    dur_arr_s    = (t_a.max() - t_a.min()) / 1000
    charge_arr_uC = np.trapezoid(c_a, x=t_a) / 1000

    # --- Ciclo SD+LoRa ---
    mask_ev = (t >= t0_ev) & (t <= t1_ev)
    t_e, c_e = t[mask_ev], c[mask_ev]
    dur_ev_s     = (t_e.max() - t_e.min()) / 1000
    charge_ev_uC  = np.trapezoid(c_e, x=t_e) / 1000

    comp = {
        "avg_base_uA":    avg_base_uA,
        "dur_arr_s":      dur_arr_s,
        "charge_arr_uC":  charge_arr_uC,
        "dur_ev_s":       dur_ev_s,
        "charge_ev_uC":   charge_ev_uC,
        "t_ev": (t_e, c_e),
        "t_base_vals": (t[mask_base], c[mask_base]),
    }
    return comp


def imprimir_componentes(comp):
    print(f"\n  Corrente de base:          {comp['avg_base_uA']:.3f} µA")
    print(f"\n  Evento arranque:")
    print(f"    Duração:                 {comp['dur_arr_s']*1000:.1f} ms")
    print(f"    Carga:                   {comp['charge_arr_uC']:.3f} µC")
    print(f"\n  Ciclo SD+LoRa (conservador):")
    print(f"    Duração:                 {comp['dur_ev_s']*1000:.1f} ms")
    print(f"    Carga:                   {comp['charge_ev_uC']:.3f} µC")
    print(f"    Corrente média no evento:{comp['charge_ev_uC']/comp['dur_ev_s']:.2f} µA")

# =============================================================================
# 3. MODELO ENERGÉTICO
# =============================================================================

def calcular_modelo(comp, cfg):
    T_s        = cfg["duracao_estudo_h"] * 3600
    n_ciclos   = cfg["duracao_estudo_h"] // cfg["intervalo_sd_h"]   # SD: 2 eventos
    # (O ciclo LoRa às 2h coincide com um dos ciclos SD — já está incluído no custo
    #  do evento combinado que usamos para ambos os n_ciclos)

    dur_arr    = comp["dur_arr_s"]
    dur_ciclo  = comp["dur_ev_s"]
    T_base_s   = T_s - dur_arr - (n_ciclos * dur_ciclo)

    E_base_uC   = comp["avg_base_uA"] * T_base_s
    E_arr_uC    = comp["charge_arr_uC"]
    E_ciclos_uC = comp["charge_ev_uC"] * n_ciclos
    E_total_uC  = E_base_uC + E_arr_uC + E_ciclos_uC

    avg_equiv_uA = E_total_uC / T_s
    mAh          = (avg_equiv_uA / 1000) * cfg["duracao_estudo_h"]
    energia_uJ   = E_total_uC * cfg["vdd_V"]

    return {
        "T_s":          T_s,
        "n_ciclos":     n_ciclos,
        "T_base_s":     T_base_s,
        "E_base_uC":    E_base_uC,
        "E_arr_uC":     E_arr_uC,
        "E_ciclos_uC":  E_ciclos_uC,
        "E_total_uC":   E_total_uC,
        "avg_equiv_uA": avg_equiv_uA,
        "mAh":          mAh,
        "energia_uJ":   energia_uJ,
    }


def imprimir_modelo(m, cfg):
    print(f"\n  Duração do estudo:         {cfg['duracao_estudo_h']}h ({m['T_s']:.0f}s)")
    print(f"  Nº ciclos SD+LoRa:         {m['n_ciclos']}")
    print(f"  Tempo em base:             {m['T_base_s']:.1f}s ({100*m['T_base_s']/m['T_s']:.2f}%)")
    print(f"\n  Energia base:              {m['E_base_uC']:.2f} µC  ({100*m['E_base_uC']/m['E_total_uC']:.2f}%)")
    print(f"  Energia arranque:          {m['E_arr_uC']:.3f} µC  ({100*m['E_arr_uC']/m['E_total_uC']:.4f}%)")
    print(f"  Energia ciclos SD+LoRa:    {m['E_ciclos_uC']:.3f} µC  ({100*m['E_ciclos_uC']/m['E_total_uC']:.4f}%)")
    print(f"  Energia total:             {m['E_total_uC']:.2f} µC = {m['energia_uJ']:.2f} µJ")
    print(f"\n  Corrente média equiv.:     {m['avg_equiv_uA']:.4f} µA")
    print(f"  Consumo {cfg['duracao_estudo_h']}h:               {m['mAh']*1000:.4f} µAh  ({m['mAh']:.6f} mAh)")

# =============================================================================
# 4. AUTONOMIA DA BATERIA
# =============================================================================

def calcular_autonomia(m, cfg):
    avg_mA        = m["avg_equiv_uA"] / 1000
    bat           = cfg["capacidade_bat_mAh"]
    fs            = cfg["fator_seguranca"]

    auto_h        = bat / avg_mA
    auto_h_fs     = auto_h * (1 - fs)
    auto_anos     = auto_h / 8760
    auto_anos_fs  = auto_h_fs / 8760

    return {
        "avg_mA":       avg_mA,
        "auto_h":       auto_h,
        "auto_h_fs":    auto_h_fs,
        "auto_anos":    auto_anos,
        "auto_anos_fs": auto_anos_fs,
    }


def imprimir_autonomia(a, cfg):
    print(f"\n  Bateria:                   {cfg['capacidade_bat_mAh']} mAh")
    print(f"  Corrente média:            {a['avg_mA']*1000:.4f} µA = {a['avg_mA']:.6f} mA")
    print(f"  Autonomia teórica:         {a['auto_h']:.0f} h = {a['auto_anos']:.1f} anos")
    print(f"  Com fator segurança {int(cfg['fator_seguranca']*100)}%:  "
          f"{a['auto_h_fs']:.0f} h = {a['auto_anos_fs']:.1f} anos")

# =============================================================================
# 5. FIGURAS
# =============================================================================

def figura_perfil_modelo(df, comp, m, cfg):
    """Fig 1: Perfil de corrente com anotação dos 3 componentes."""
    t = df["Timestamp(ms)"].values / 1000
    c = df["Current(uA)"].values / 1000

    fig, ax = plt.subplots(figsize=(13, 5))
    ax.plot(t, c, linewidth=0.5, color="#3266ad", alpha=0.8, rasterized=True, label="Corrente medida")
    ax.axhline(comp["avg_base_uA"] / 1000, color="green", linewidth=1.2,
               linestyle="--", label=f"Base = {comp['avg_base_uA']:.2f} µA")

    # Sombrear arranque
    t0_a, t1_a = cfg["janela_arranque_ms"]
    ax.axvspan(t0_a/1000, t1_a/1000, alpha=0.15, color="red", label="Arranque")

    # Sombrear evento SD+LoRa
    t0_e, t1_e = cfg["janela_evento_ms"]
    ax.axvspan(t0_e/1000, t1_e/1000, alpha=0.15, color="orange", label="Ciclo SD+LoRa")

    ax.set_xlabel("Tempo (s)", fontsize=10)
    ax.set_ylabel("Corrente (mA)", fontsize=10)
    ax.set_title("Perfil de corrente — componentes do modelo energético", fontsize=11)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.set_ylim(bottom=0)
    plt.tight_layout()
    out = os.path.join(cfg["output_dir"], "proj_fig1_perfil_componentes.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Guardada: {out}")


def figura_decomposicao_energia(m, cfg):
    """Fig 2: Gráfico de barras com decomposição da energia total."""
    labels  = ["Base", "Arranque", f"Ciclos SD+LoRa\n(×{m['n_ciclos']})"]
    valores = [m["E_base_uC"], m["E_arr_uC"], m["E_ciclos_uC"]]
    cores   = ["#3266ad", "#d85a30", "#3a9e5f"]
    pct     = [100 * v / m["E_total_uC"] for v in valores]

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(labels, valores, color=cores, alpha=0.8, edgecolor="white")
    for bar, v, p in zip(bars, valores, pct):
        ax.text(bar.get_x() + bar.get_width()/2,
                bar.get_height() + m["E_total_uC"] * 0.01,
                f"{v:.1f} µC\n({p:.2f}%)",
                ha="center", va="bottom", fontsize=9)

    ax.set_ylabel("Carga (µC)", fontsize=10)
    ax.set_title(f"Decomposição da energia total em {cfg['duracao_estudo_h']}h\n"
                 f"(Total: {m['E_total_uC']:.1f} µC = {m['mAh']*1000:.2f} µAh)", fontsize=11)
    ax.grid(True, alpha=0.3, axis="y")
    plt.tight_layout()
    out = os.path.join(cfg["output_dir"], "proj_fig2_decomposicao_energia.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Guardada: {out}")


def figura_autonomia(a, cfg):
    """Fig 3: Autonomia estimada com e sem fator de segurança."""
    labels  = ["Teórica", f"Com fator\nsegurança {int(cfg['fator_seguranca']*100)}%"]
    valores = [a["auto_anos"], a["auto_anos_fs"]]
    cores   = ["#3266ad", "#d85a30"]

    fig, ax = plt.subplots(figsize=(7, 5))
    bars = ax.bar(labels, valores, color=cores, alpha=0.8, edgecolor="white", width=0.4)
    for bar, v in zip(bars, valores):
        ax.text(bar.get_x() + bar.get_width()/2,
                bar.get_height() + 0.1,
                f"{v:.1f} anos\n({v*8760:.0f} h)",
                ha="center", va="bottom", fontsize=10)

    ax.set_ylabel("Autonomia (anos)", fontsize=10)
    ax.set_title(f"Autonomia estimada — Bateria {cfg['capacidade_bat_mAh']} mAh\n"
                 f"Corrente média: {a['avg_mA']*1000:.3f} µA", fontsize=11)
    ax.grid(True, alpha=0.3, axis="y")
    ax.set_ylim(0, max(valores) * 1.3)
    plt.tight_layout()
    out = os.path.join(cfg["output_dir"], "proj_fig3_autonomia.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Guardada: {out}")


def figura_zoom_evento(comp, cfg):
    """Fig 4: Zoom no ciclo SD+LoRa com carga acumulada."""
    t_e, c_e = comp["t_ev"]
    t_s = (t_e - t_e.min()) / 1000   # normalizar para 0

    charge_cum = np.cumsum(c_e * np.gradient(t_e)) / 1000  # µC acumulados

    fig, ax1 = plt.subplots(figsize=(10, 5))
    ax2 = ax1.twinx()

    ax1.plot(t_s, c_e / 1000, color="#3266ad", linewidth=1.2, label="Corrente (mA)")
    ax2.plot(t_s, charge_cum, color="#d85a30", linewidth=1.5,
             linestyle="--", label="Carga acumulada (µC)")

    ax1.set_xlabel("Tempo no evento (s)", fontsize=10)
    ax1.set_ylabel("Corrente (mA)", fontsize=10, color="#3266ad")
    ax1.tick_params(axis="y", labelcolor="#3266ad")
    ax2.set_ylabel("Carga acumulada (µC)", fontsize=10, color="#d85a30")
    ax2.tick_params(axis="y", labelcolor="#d85a30")

    lines1, labs1 = ax1.get_legend_handles_labels()
    lines2, labs2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labs1 + labs2, fontsize=9)

    ax1.set_title(f"Zoom no ciclo SD+LoRa — Carga total: {comp['charge_ev_uC']:.2f} µC "
                  f"em {comp['dur_ev_s']*1000:.0f} ms", fontsize=11)
    ax1.grid(True, alpha=0.3)
    plt.tight_layout()
    out = os.path.join(cfg["output_dir"], "proj_fig4_zoom_evento_sdlora.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Guardada: {out}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("\n" + "="*60)
    print("  PROJEÇÃO ENERGÉTICA 2H — Nordic PPK2")
    print("  Modelo: Base + Arranque + Ciclos SD+LoRa")
    print("="*60)

    print("\n" + "="*60)
    print("1. CARREGAMENTO")
    print("="*60)
    df = carregar(CONFIG["ficheiro"])

    print("\n" + "="*60)
    print("2. EXTRAÇÃO DOS COMPONENTES")
    print("="*60)
    comp = extrair_componentes(df, CONFIG)
    imprimir_componentes(comp)

    print("\n" + "="*60)
    print("3. MODELO ENERGÉTICO")
    print("="*60)
    modelo = calcular_modelo(comp, CONFIG)
    imprimir_modelo(modelo, CONFIG)

    print("\n" + "="*60)
    print("4. AUTONOMIA DA BATERIA")
    print("="*60)
    auto = calcular_autonomia(modelo, CONFIG)
    imprimir_autonomia(auto, CONFIG)

    print("\n" + "="*60)
    print("5. FIGURAS")
    print("="*60)
    figura_perfil_modelo(df, comp, modelo, CONFIG)
    figura_decomposicao_energia(modelo, CONFIG)
    figura_autonomia(auto, CONFIG)
    figura_zoom_evento(comp, CONFIG)

    print("\n" + "="*60)
    print("  CONCLUÍDO")
    print("="*60)


if __name__ == "__main__":
    main()