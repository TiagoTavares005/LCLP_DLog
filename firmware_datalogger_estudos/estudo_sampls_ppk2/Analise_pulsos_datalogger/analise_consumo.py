"""
Análise de dados de consumo — LCLP-DLog
=========================================
Lê o ficheiro CSV (formato: YYYY/MM/DD,HH:MM:SS,pulsos)
Utilização:
    python analise_consumo.py dados_consumo.csv
"""

import sys
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def carregar_dados(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, header=None, names=["data", "hora", "pulsos"])
    df["timestamp"] = pd.to_datetime(df["data"] + " " + df["hora"], format="%Y/%m/%d %H:%M:%S")
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["litros"] = df["pulsos"]  # 1 pulso = 1 litro

    # Exclui registos isolados com RTC ainda não sincronizado: qualquer
    # registo seguido de um gap > 24h é considerado anterior à sincronização
    # real do relógio (ex: primeiro boot, timestamp por defeito), não uma
    # medição válida no tempo.
    gaps = df["timestamp"].diff().dt.total_seconds() / 3600.0
    proximo_gap = gaps.shift(-1)
    mask_valido = proximo_gap.isna() | (proximo_gap < 24)
    n_excluidos = (~mask_valido).sum()
    if n_excluidos > 0:
        print(f"[AVISO] {n_excluidos} registo(s) excluído(s) por RTC não sincronizado "
              f"(gap > 24h para o registo seguinte):")
        for _, row in df[~mask_valido].iterrows():
            print(f"    {row['timestamp']}  ({row['pulsos']} pulsos)")
        df = df[mask_valido].reset_index(drop=True)

    return df


def estatisticas_gerais(df: pd.DataFrame):
    print("=" * 60)
    print("ESTATÍSTICAS GERAIS")
    print("=" * 60)
    print(f"Nº de registos        : {len(df)}")
    print(f"Período               : {df['timestamp'].min()} a {df['timestamp'].max()}")
    print(f"Total de pulsos/litros: {df['pulsos'].sum():,}")
    print(f"Média por registo     : {df['pulsos'].mean():.1f}")
    print(f"Mediana por registo   : {df['pulsos'].median():.1f}")
    print(f"Desvio padrão         : {df['pulsos'].std():.1f}")
    print(f"Mínimo / Máximo       : {df['pulsos'].min()} / {df['pulsos'].max()}")
    print(f"Percentis 25/75/95    : {df['pulsos'].quantile(0.25):.0f} / "
          f"{df['pulsos'].quantile(0.75):.0f} / {df['pulsos'].quantile(0.95):.0f}")


def deteta_gaps(df: pd.DataFrame, esperado_min: int = 5):
    """Deteta falhas/gaps na série temporal (registos em falta)."""
    print("\n" + "=" * 60)
    print(f"DETEÇÃO DE GAPS (intervalo esperado ~{esperado_min} min)")
    print("=" * 60)
    diffs = df["timestamp"].diff().dt.total_seconds() / 60.0
    gaps = df[diffs > esperado_min * 2].copy()
    gaps["gap_min"] = diffs[diffs > esperado_min * 2]
    if gaps.empty:
        print("Nenhum gap relevante detetado.")
    else:
        print(f"{len(gaps)} gap(s) encontrados:")
        for _, row in gaps.iterrows():
            print(f"  {row['timestamp']}  (gap de {row['gap_min']:.0f} min)")


def deteta_anomalias(df: pd.DataFrame, limiar_desvios: float = 3.0):
    """Deteta possíveis fugas: valores muito acima da média (outliers)."""
    print("\n" + "=" * 60)
    print(f"DETEÇÃO DE ANOMALIAS (> {limiar_desvios} desvios-padrão)")
    print("=" * 60)
    media, desvio = df["pulsos"].mean(), df["pulsos"].std()
    limite = media + limiar_desvios * desvio
    anomalias = df[df["pulsos"] > limite]
    print(f"Limite calculado: {limite:.0f} pulsos")
    if anomalias.empty:
        print("Nenhuma anomalia detetada.")
    else:
        print(f"{len(anomalias)} registo(s) acima do limite:")
        for _, row in anomalias.iterrows():
            print(f"  {row['timestamp']}  →  {row['pulsos']} pulsos")


def consumo_diario(df: pd.DataFrame) -> pd.DataFrame:
    print("\n" + "=" * 60)
    print("CONSUMO POR DIA")
    print("=" * 60)
    diario = df.groupby(df["timestamp"].dt.date)["pulsos"].sum()
    for dia, total in diario.items():
        print(f"  {dia}: {total:,} L")
    return diario


def consumo_por_hora(df: pd.DataFrame) -> pd.Series:
    print("\n" + "=" * 60)
    print("PERFIL MÉDIO POR HORA DO DIA")
    print("=" * 60)
    perfil = df.groupby(df["timestamp"].dt.hour)["pulsos"].mean()
    for h, v in perfil.items():
        print(f"  {h:02d}h: {v:.1f} L (média)")
    return perfil


def figuras(df: pd.DataFrame, diario: pd.Series, perfil_hora: pd.Series, output_dir="."):
    # Fig 1 — série temporal completa
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(df["timestamp"], df["pulsos"], linewidth=0.8, color="#0284C7")
    ax.set_xlabel("Data/Hora")
    ax.set_ylabel("Pulsos (litros) por registo")
    ax.set_title("Consumo de água ao longo do tempo")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    plt.tight_layout()
    fig.savefig(f"{output_dir}/fig1_serie_temporal.png", dpi=150)
    plt.close(fig)

    # Fig 2 — consumo diário (barras)
    fig, ax = plt.subplots(figsize=(10, 5))
    diario.plot(kind="bar", ax=ax, color="#16A34A")
    ax.set_ylabel("Total de litros")
    ax.set_title("Consumo total por dia")
    ax.grid(True, alpha=0.3, axis="y")
    plt.tight_layout()
    fig.savefig(f"{output_dir}/fig2_consumo_diario.png", dpi=150)
    plt.close(fig)

    # Fig 3 — perfil horário médio
    fig, ax = plt.subplots(figsize=(10, 5))
    perfil_hora.plot(kind="bar", ax=ax, color="#7C3AED")
    ax.set_xlabel("Hora do dia")
    ax.set_ylabel("Consumo médio (litros)")
    ax.set_title("Perfil médio de consumo por hora do dia")
    ax.grid(True, alpha=0.3, axis="y")
    plt.tight_layout()
    fig.savefig(f"{output_dir}/fig3_perfil_horario.png", dpi=150)
    plt.close(fig)

    # Fig 4 — histograma de distribuição
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(df["pulsos"], bins=50, color="#F59E0B", edgecolor="white")
    ax.set_xlabel("Pulsos por registo")
    ax.set_ylabel("Frequência")
    ax.set_title("Distribuição do consumo por registo")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    fig.savefig(f"{output_dir}/fig4_distribuicao.png", dpi=150)
    plt.close(fig)

    print("\nFiguras guardadas: fig1_serie_temporal.png, fig2_consumo_diario.png, "
          "fig3_perfil_horario.png, fig4_distribuicao.png")


NOME_FICHEIRO = "consumos_pcb_09_07_2026.txt"


def main():
    df = carregar_dados(NOME_FICHEIRO)
    estatisticas_gerais(df)
    deteta_gaps(df)
    deteta_anomalias(df)
    diario = consumo_diario(df)
    perfil_hora = consumo_por_hora(df)
    figuras(df, diario, perfil_hora)


if __name__ == "__main__":
    main()