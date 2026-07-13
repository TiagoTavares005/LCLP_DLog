import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# ==========================================
# 1. CARREGAR DADOS
# ==========================================
ficheiro = 'Contagens__new.csv'

df = pd.read_csv(ficheiro)

# Limpar nomes de colunas (evita espaços invisíveis)
df.columns = df.columns.str.strip()

print("Colunas detetadas:", df.columns.tolist())

# ==========================================
# 2. PARSE DATETIME (ROBUSTO)
# ==========================================
if 'Data hora' in df.columns:
    df['Datetime'] = pd.to_datetime(df['Data hora'], errors='coerce')

elif 'Data' in df.columns and 'Hora' in df.columns:
    df['Datetime'] = pd.to_datetime(
        df['Data'] + ' ' + df['Hora'],
        errors='coerce'
    )

else:
    raise ValueError("Não encontrei colunas de data válidas no CSV!")

# Remover linhas inválidas
df = df.dropna(subset=['Datetime'])

# Garantir ordenação temporal 
df = df.sort_values('Datetime')

# Definir índice temporal
df.set_index('Datetime', inplace=True)

# ==========================================
# 3. VALIDAR COLUNA DE DADOS
# ==========================================
if 'Contagens' not in df.columns:
    raise ValueError("Coluna 'Contagens' não encontrada!")

# Converter para inteiro seguro
df['Contagens'] = pd.to_numeric(df['Contagens'], errors='coerce')
df = df.dropna(subset=['Contagens'])

# ==========================================
# 4. RESAMPLE (JANELAS DE 15 MIN)
# ==========================================
df_15m = df['Contagens'].resample('15min').sum()

# Remover janelas sem dados reais
mask_valid = df['Contagens'].resample('15min').count() > 0
df_15m = df_15m[mask_valid]

# Converter para array inteiro
contagens = np.round(df_15m.values).astype(int)

# ==========================================
# 5. CÁLCULOS MATEMÁTICOS
# ==========================================
if len(contagens) == 0:
    raise ValueError("Sem dados após resample!")

max_val = np.max(contagens)

bits_brutos = int(np.ceil(np.log2(max_val + 1))) if max_val > 0 else 1

valores_unicos, frequencias = np.unique(contagens, return_counts=True)
probabilidades = frequencias / len(contagens)

entropia = -np.sum(probabilidades * np.log2(probabilidades))

# ==========================================
# 6. RESULTADOS
# ==========================================
print("\n--- RESULTADOS PARA O RELATÓRIO ---")
print(f"Total de janelas (15 min): {len(contagens)}")
print(f"Valor máximo: {max_val} pulsos")
print(f"Bits necessários (bit-packing): {bits_brutos} bits")
print(f"Entropia de Shannon: {entropia:.2f} bits")
print(f"Poupança teórica (Huffman): {bits_brutos - entropia:.2f} bits/amostra")

# ==========================================
# 7. GRÁFICO
# ==========================================
plt.figure(figsize=(10, 6))

plt.hist(contagens, bins=50, edgecolor='black', alpha=0.7)

plt.title('Distribuição de Consumo de Água (15 min)')
plt.xlabel('Pulsos por janela')
plt.ylabel('Frequência')

plt.axvline(max_val, linestyle='dashed', linewidth=2, label=f'Máximo: {max_val}')
plt.legend()

plt.grid(axis='y', alpha=0.5)

plt.savefig('grafico_dispersao_pulsos.png', dpi=300, bbox_inches='tight')
plt.show()