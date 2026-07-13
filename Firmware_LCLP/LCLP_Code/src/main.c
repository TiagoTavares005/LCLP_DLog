
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/drivers/gpio.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zephyr/fs/fs.h>
#include <zephyr/storage/disk_access.h>
#include <ff.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/gap.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/drivers/lora.h>
#include <zephyr/lorawan/lorawan.h>
#include <zephyr/settings/settings.h>
#include <zephyr/posix/time.h>
#include <zephyr/pm/device.h>
#include <time.h>
#include <zephyr/drivers/regulator.h>
#include <zephyr/drivers/sensor.h>
#include <nrf_fuel_gauge.h>


/* =========================================================================
 * 1. DEFINIÇÕES DE UUIDS (ORDEM OBRIGATÓRIA PARA O COMPILADOR)
 * ========================================================================= */
#define BT_UUID_AQUALOG_SERVICE_VAL BT_UUID_128_ENCODE(0x999131c3, 0xc9c5, 0xcc8f, 0x9e45, 0xb51f01c2af4f)
#define BT_UUID_AQUALOG_ID_CHAR_VAL BT_UUID_128_ENCODE(0x4c9131c3, 0xc9c5, 0xcc8f, 0x9e45, 0xb51f01c2af4f)
#define BT_UUID_AQUALOG_DATA_CHAR_VAL BT_UUID_128_ENCODE(0x4d9131c3, 0xc9c5, 0xcc8f, 0x9e45, 0xb51f01c2af4f)
#define BT_UUID_AQUALOG_CONFIG_CHAR_VAL BT_UUID_128_ENCODE(0x4e9131c3, 0xc9c5, 0xcc8f, 0x9e45, 0xb51f01c2af4f)

#define BT_UUID_AQUALOG_SERVICE BT_UUID_DECLARE_128(BT_UUID_AQUALOG_SERVICE_VAL)
#define BT_UUID_AQUALOG_ID_CHAR BT_UUID_DECLARE_128(BT_UUID_AQUALOG_ID_CHAR_VAL)
#define BT_UUID_AQUALOG_DATA_CHAR BT_UUID_DECLARE_128(BT_UUID_AQUALOG_DATA_CHAR_VAL)
#define BT_UUID_AQUALOG_CONFIG_CHAR BT_UUID_DECLARE_128(BT_UUID_AQUALOG_CONFIG_CHAR_VAL)

/* =========================================================================
 * 2. HARDWARE E DEFINIÇÕES DE SISTEMA
 * ========================================================================= */
#define BOTAO_BLE_NODE DT_ALIAS(botaoble)
#define LED1_NODE DT_ALIAS(led2)

#define DISK_MOUNT_PT "/SD:"
#define LOG_FILE_PATH DISK_MOUNT_PT "/teste1.txt"

#define MAX_LOTE_SD 24 // Buffer para aguentar várias amostras na RAM
// O limite máximo que impuseste ao LoRaWAN (2 horas / 5 minutos = 24 amostras)
#define MAX_AMOSTRAS_LORA 24 
/* Stacks e Prioridades (Ficheiro A) */
#define STACK_LORA 4096
#define STACK_BLE 2048
#define STACK_BRAIN 2048
#define STACK_SD 4096
#define STACK_MOTOR 2048
#define PRIO_MOTOR 4
#define PRIO_BRAIN 5
#define PRIO_LORA 6
#define PRIO_SD 7
#define PRIO_BLE 8
static const struct battery_model bateria_aqualog = {
#include "battery_model.inc"
};
static const struct device *fuel_gauge_dev = DEVICE_DT_GET(DT_NODELABEL(npm1300_charger));
static const struct gpio_dt_spec sd_cs_pin = GPIO_DT_SPEC_GET_BY_IDX(DT_NODELABEL(spi22), cs_gpios, 1);
// NOVO: Apontador direto para o VOUT1 (BUCK1)
static const struct device *sd_regulator = DEVICE_DT_GET(DT_NODELABEL(npm1300_buck1));
static uint8_t dev_eui[] = {0x70, 0xB3, 0xD5, 0x7E, 0xD0, 0x07, 0x63, 0x0B};
static uint8_t join_eui[] = {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};
static uint8_t app_key[] = {0x25, 0x79, 0x95, 0xE7, 0xAA, 0xE4, 0x95, 0xB8, 0xE9, 0xEC, 0x60, 0x0F, 0xA6, 0x6A, 0xD6, 0x33};

static FATFS fat_fs;
static struct fs_mount_t mp = {.type = FS_FATFS, .mnt_point = DISK_MOUNT_PT, .fs_data = &fat_fs};
static struct fs_file_t log_file;

static const struct gpio_dt_spec button3 = GPIO_DT_SPEC_GET(BOTAO_BLE_NODE, gpios);
static struct gpio_callback button3_cb;

static bool ble_connected = false;
static bool ble_advertising = false;
static bool notify_enabled = false;
static char data_tx_buffer[64];
static char cmd_rx_buffer[128];

// Variáveis de Configuração Dinâmica
static int64_t ref_time_bateria;
static bool fuel_gauge_inicializado = false;
static uint32_t sampling_interval_ms = 2000;
static uint16_t intervalo_aquisicao_min = 5; // 5 ou 15 min
static uint16_t intervalo_escrita_sd_min =60; // 30, 60, ou 120 min
static uint16_t intervalo_lora_min = 120;   // 30, 60, ou 120 min
static bool tempo_sincronizado = false;
static int bat_level = 98;
static atomic_t mudanca_pendente_lora = ATOMIC_INIT(0);
static atomic_t mudanca_pendente_sd = ATOMIC_INIT(0);
K_MUTEX_DEFINE(config_mutex);
K_MUTEX_DEFINE(uart_mutex);
K_MUTEX_DEFINE(power_lock_mutex);
K_MUTEX_DEFINE(i2c_mutex);
K_SEM_DEFINE(sd_flush_sem, 0, 1); 
K_SEM_DEFINE(ble_data_ready_sem, 0, 1);
K_SEM_DEFINE(ble_start_adv_sem, 0, 1);
K_SEM_DEFINE(brain_sync_sem, 0, 1);
K_SEM_DEFINE(sd_card_inserted_sem, 0, 1);
K_TIMER_DEFINE(motor_timer, NULL, NULL);
// Variaveis ATÓMICAS: Ponte segura entre Hardware e Threads (PULSOS)
static const struct gpio_dt_spec pino_agua = GPIO_DT_SPEC_GET(DT_ALIAS(sensoragua), gpios);
static struct gpio_callback agua_cb_data;


static const struct gpio_dt_spec sd_cd_pin = GPIO_DT_SPEC_GET(DT_ALIAS(cartaosdcd), gpios);
static struct gpio_callback sd_cd_cb_data;


static atomic_t pulsos_delta_ble = ATOMIC_INIT(0);
static atomic_t pulsos_totais_hw = ATOMIC_INIT(0);
struct LogRecord
{
    uint32_t pulses;         // 4 bytes
    uint16_t year;           // 2 bytes
    uint16_t novo_intervalo; // 2 bytes 
    uint8_t month, day, hour, min, sec; // 5 bytes
    bool config_change;      // 1 byte
};

struct LoRaPayload {
    // --- CABEÇALHO ---
    // Na RAM ocupam 1 byte cada, mas na função encode_payload_lora 
    // vão ser comprimido (Bit-Packing) para ocuparem apenas 4 BYTES no total!
    uint8_t  year_offset; // year - 2000, ex: 26 para 2026
    uint8_t  month;       // 1–12
    uint8_t  day;         // 1–31
    uint8_t  hour;        // 0–23
    uint8_t  min;         // 0–59
    uint8_t  bat_level;          // 0–100%
    uint8_t  intervalo_acq_min;  // 5 ou 15 
    
    // --- CONTROLO INTERNO ---
    uint8_t  num_amostras;   

    // --- DADOS ---
    // Em RAM ocupam 16-bit (2 bytes) por amostra. 
    // No encoder sofrem 12-bit packing (Cada 2 amostras = 3 Bytes no ar).
    uint16_t pulsos_amostras[MAX_AMOSTRAS_LORA]; 
};



K_MSGQ_DEFINE(sd_msgq, sizeof(struct LogRecord), 60, 4);
K_MSGQ_DEFINE(lora_msgq, sizeof(struct LoRaPayload), 2, 4);

/* =========================================================================
 * 4. AUXILIARES: RTC FÍSICO E HARDWARE COUNTER
 * ========================================================================= */
static inline int bcd_to_dec(uint8_t val) { return (val & 0x0F) + ((val >> 4) * 10); }
static inline uint8_t dec_to_bcd(int val) { return ((val / 10) << 4) | (val % 10); }
static int encode_payload_lora(const struct LoRaPayload *p,
                                uint8_t *out, size_t out_max)
{
    int pos = 0;

    /* --- Cabeçalho Comprimido: Exatamente 4 bytes (32 bits) --- */
    if (out_max < 4) return -1;

    // 1. Limitar variáveis e ajustar base do ano (Assumimos ano base 2024)
    // O teu p->year_offset é (Ano - 2000), logo 2026 = 26. Fazemos 26 - 24 = 2.
    uint32_t year_val = (p->year_offset >= 24) ? (p->year_offset - 24) : 0; 
    
    uint32_t y    = year_val & 0x0F;              // 4 bits (Max 15 -> Ano 2039)
    uint32_t m    = p->month & 0x0F;              // 4 bits (Max 15)
    uint32_t d    = p->day & 0x1F;                // 5 bits (Max 31)
    uint32_t h    = p->hour & 0x1F;               // 5 bits (Max 31)
    uint32_t mi   = p->min & 0x3F;                // 6 bits (Max 63)
    uint32_t flag = (p->intervalo_acq_min == 15) ? 1 : 0; // 1 bit (0=5m, 1=15m)
    uint32_t bat  = p->bat_level & 0x7F;          // 7 bits (Max 127)

    // 2. Empilhar tudo usando Bit-Shifting (<<)
    uint32_t header = (y << 28) | (m << 24) | (d << 19) | (h << 14) | (mi << 8) | (flag << 7) | bat;

    // 3. Gravar os 4 bytes no buffer (formato Big-Endian para ser fácil de ler na nuvem)
    out[pos++] = (header >> 24) & 0xFF;
    out[pos++] = (header >> 16) & 0xFF;
    out[pos++] = (header >> 8) & 0xFF;
    out[pos++] = header & 0xFF;

    /* --- Amostras: packing 12-bit mantido igual! --- */
    for (int i = 0; i < p->num_amostras; i += 2) {
        uint16_t a = (p->pulsos_amostras[i] > 0xFFF) ? 0xFFF : p->pulsos_amostras[i];
        uint16_t b = ((i + 1) < p->num_amostras) 
                     ? ((p->pulsos_amostras[i+1] > 0xFFF) ? 0xFFF : p->pulsos_amostras[i+1])
                     : 0;

        if (pos + 3 > (int)out_max) break; 

        out[pos++] = (a >> 4) & 0xFF;              // 8 bits altos de A
        out[pos++] = ((a & 0x0F) << 4) | (b >> 8); // 4 bits baixos de A + 4 altos de B
        if ((i + 1) < p->num_amostras) {
            if (pos >= (int)out_max) break;
            out[pos++] = b & 0xFF;                 
        }                     
    }

    return pos; // Retorna o nº total de bytes (agora muito mais pequeno!)
}

void pino_agua_isr(const struct device *dev, struct gpio_callback *cb, uint32_t pins)
{
    atomic_inc(&pulsos_totais_hw);
}

void ler_rtc_para_pacote(struct LogRecord *pacote)
{
    if (tempo_sincronizado)
    {
        struct timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);

        // --- CORREÇÃO: Removemos o bloco de arredondamento forçado (t_raw += 60...) ---
        time_t t_raw = ts.tv_sec;

        struct tm t;
        gmtime_r(&t_raw, &t);

        // Guardamos os segundos REAIS vindos do chip para alinhar o sono perfeitamente
        pacote->sec = t.tm_sec; 
        pacote->min = t.tm_min;
        pacote->hour = t.tm_hour;
        pacote->day = t.tm_mday;
        pacote->month = t.tm_mon + 1;       
        pacote->year = t.tm_year + 1900;    
    }
    else
    {
        uint64_t uptime_ms = k_uptime_get();
        uint32_t total_sec = (uint32_t)(uptime_ms / 1000);

        pacote->sec = total_sec % 60;
        pacote->min = (total_sec / 60) % 60;
        pacote->hour = (total_sec / 3600) % 24;
        pacote->day = 1;
        pacote->month = 1;
        pacote->year = 2026; 

        static uint32_t last_err_print = 0;
        if (total_sec - last_err_print > 60)
        { 
            printk("[!] AVISO: GRTC não sincronizado. A usar Uptime Fallback.\n");
            last_err_print = total_sec;
        }
    }
}
void escrever_rtc(int a, int m, int d, int h, int mi, int s)
{
    struct tm t = {0};
    t.tm_year = (2000 + a) - 1900; // A biblioteca C conta anos desde 1900
    t.tm_mon = m - 1;              // A biblioteca C conta meses de 0 a 11
    t.tm_mday = d;                 // Dias de 1 a 31
    t.tm_hour = h;
    t.tm_min = mi;
    t.tm_sec = s;

    // Converte a data humana num "Unix Timestamp" (segundos desde 1970)
    time_t unix_time = mktime(&t);

    // Injeta esse tempo no relógio do sistema Zephyr (GRTC)
    struct timespec ts;
    ts.tv_sec = unix_time;
    ts.tv_nsec = 0;
    clock_settime(CLOCK_REALTIME, &ts);
    
    tempo_sincronizado = true;

    printk("[GRTC] Relógio interno sincronizado via BLE: 20%02d/%02d/%02d %02d:%02d:%02d\n", a, m, d, h, mi, s);
}


void mount_and_open_sd(void)
{
    k_msleep(2000);
    int rc;
    do
    {
        rc = disk_access_init("SD");
        if (rc != 0)
            k_msleep(1000);
    } while (rc != 0);
    while (fs_mount(&mp) != 0)
        k_msleep(2000);
    fs_file_t_init(&log_file);
    fs_open(&log_file, LOG_FILE_PATH, FS_O_WRITE | FS_O_CREATE | FS_O_APPEND);
}

void npm1300_vout1_ligar(void) {
    const struct device *i2c_dev = DEVICE_DT_GET(DT_NODELABEL(i2c21));

    if (!device_is_ready(sd_regulator)) {
        printk("[PMIC] Erro: Regulador VOUT1 não está pronto!\n");
        return;
    }

    // 1. ACORDA O I2C
    pm_device_action_run(i2c_dev, PM_DEVICE_ACTION_RESUME);

    // 2. Envia o comando via I2C para o PMIC ligar a energia
    int ret = regulator_enable(sd_regulator);
    
    // 3. ADORMECE LOGO O I2C 
    pm_device_action_run(i2c_dev, PM_DEVICE_ACTION_SUSPEND);

    if (ret == 0 || ret == -EALREADY) {
        printk("[PMIC] VOUT1 LIGADO (3.3V ao SD).\n");
        // O I2C já está a dormir enquanto o sistema espera a estabilização elétrica:
        k_sleep(K_MSEC(150)); 
    } else {
        printk("[PMIC] Erro ao ligar VOUT1: %d\n", ret);
    }
}

void npm1300_vout1_desligar(void) {
    const struct device *i2c_dev = DEVICE_DT_GET(DT_NODELABEL(i2c21));

    if (!device_is_ready(sd_regulator)) {
        return;
    }

    // 1. ACORDA O I2C
    pm_device_action_run(i2c_dev, PM_DEVICE_ACTION_RESUME);

    // 2. Envia o comando de corte
    int ret = regulator_disable(sd_regulator);
    
    // 3. ADORMECE LOGO O I2C
    pm_device_action_run(i2c_dev, PM_DEVICE_ACTION_SUSPEND);

    if (ret == 0 || ret == -EALREADY) {
        printk("[PMIC] VOUT1 DESLIGADO.\n");
    } else {
        printk("[PMIC] Erro ao desligar VOUT1: %d\n", ret);
    }
}
void ler_percentagem_bateria_real(void) {
    if (!device_is_ready(fuel_gauge_dev)) return;
    float soc_inicial = 0.0f;
    k_mutex_lock(&i2c_mutex, K_FOREVER);
    const struct device *i2c_dev = DEVICE_DT_GET(DT_NODELABEL(i2c21));

    // 1. ACORDA O I2C PARA MEDIR A FÍSICA
    pm_device_action_run(i2c_dev, PM_DEVICE_ACTION_RESUME);
    int err = sensor_sample_fetch(fuel_gauge_dev);
    
    struct sensor_value v_val, i_val, t_val;
    
    if (err == 0) {
        sensor_channel_get(fuel_gauge_dev, SENSOR_CHAN_GAUGE_VOLTAGE, &v_val);
        sensor_channel_get(fuel_gauge_dev, SENSOR_CHAN_GAUGE_AVG_CURRENT, &i_val);
        sensor_channel_get(fuel_gauge_dev, SENSOR_CHAN_GAUGE_TEMP, &t_val);
    }

    // 2. ADORMECE O I2C (Poupa energia enquanto fazemos contas)
    pm_device_action_run(i2c_dev, PM_DEVICE_ACTION_SUSPEND);
    k_mutex_unlock(&i2c_mutex);

    // Se houve erro na leitura, aborta
    if (err != 0) {
        printk("[BATERIA] Erro a ler PMIC: %d\n", err);
        return;
    }

    // 3. CONVERTER PARA VÍRGULA FLUTUANTE (FLOAT) COMO A NORDIC EXIGE
    float voltage = (float)v_val.val1 + ((float)v_val.val2 / 1000000.0f);
    float current = (float)i_val.val1 + ((float)i_val.val2 / 1000000.0f);
    float temp    = (float)t_val.val1 + ((float)t_val.val2 / 1000000.0f);

    // O nPM1300 reporta descarga como negativo, mas a biblioteca Nordic 
    // espera que descarga seja negativo e carga positivo. A inversão é necessária:
    current = -current; 

    // 4. INICIALIZAÇÃO ÚNICA DO ALGORITMO (Só corre na 1ª vez que a placa liga)
    if (!fuel_gauge_inicializado) {
        struct nrf_fuel_gauge_init_parameters init_params = {
            .model = &bateria_aqualog,
            .v0 = voltage,
            .i0 = current,
            .t0 = temp,
        };
        nrf_fuel_gauge_init(&init_params, &soc_inicial);
        ref_time_bateria = k_uptime_get();
        fuel_gauge_inicializado = true;
        printk("[BATERIA] Fuel Gauge Inicializado (V: %.2fV)\n", voltage);
    }

    // 5. CALCULAR O TEMPO PASSADO DESDE A ÚLTIMA LEITURA (Delta em segundos)
    float delta_segundos = (float)k_uptime_delta(&ref_time_bateria) / 1000.0f;

    //O ALGORITMO CALCULA A PERCENTAGEM!
    float soc = nrf_fuel_gauge_process(voltage, current, temp, delta_segundos, NULL);

    //GUARDAR E HIGIENIZAR O VALOR FINAL
    int percentagem = (int)soc;
    if (percentagem < 0) percentagem = 0;
    if (percentagem > 100) percentagem = 100;
    
    bat_level = percentagem;
}

/* =========================================================================
 * 5. BLUETOOTH CALLBACKS E PARSER COMPLETO
 * ========================================================================= */
static void connected_cb(struct bt_conn *conn, uint8_t err)
{
    if (!err)
    {
        ble_connected = true;
        k_sem_give(&ble_data_ready_sem); // ACORDA A THREAD MOTOR
        printk("[BLE] Conectado!\n");
    }
}
static void disconnected_cb(struct bt_conn *conn, uint8_t reason)
{
    ble_connected = false;
    ble_advertising = false;
    notify_enabled = false;
    printk("[BLE] Desconectado.\n");
}
static struct bt_conn_cb conn_callbacks = {.connected = connected_cb, .disconnected = disconnected_cb};

static ssize_t read_id_cb(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf, uint16_t len, uint16_t offset)
{
    const char *id = "10:57:79:39:16";
    return bt_gatt_attr_read(conn, attr, buf, len, offset, id, strlen(id));
}
static void data_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
    notify_enabled = (value == BT_GATT_CCC_NOTIFY);
}

static ssize_t write_config_cb(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                               const void *buf, uint16_t len, uint16_t offset, uint8_t flags)
{
    // 1. HIGIENE DE MEMÓRIA: Limpeza e Cópia segura do comando
    memset(cmd_rx_buffer, 0, sizeof(cmd_rx_buffer));
    size_t copy_len = (len < (sizeof(cmd_rx_buffer) - 1)) ? len : (sizeof(cmd_rx_buffer) - 1);
    memcpy(cmd_rx_buffer, buf, copy_len);

    // 2. SEGURANÇA: Bloqueio para alteração de variáveis globais
    k_mutex_lock(&config_mutex, K_FOREVER);

    // --- PARSER DE COMANDOS ---

    // A: ACERTO DE RELÓGIO (Formato S:YYMMDDHHMMSS)
    if (strncmp(cmd_rx_buffer, "S:", 2) == 0 && len >= 14)
    {
        char t[3] = {0};
        memcpy(t, &cmd_rx_buffer[2], 2);
        int a = atoi(t);
        memcpy(t, &cmd_rx_buffer[4], 2);
        int m = atoi(t);
        memcpy(t, &cmd_rx_buffer[6], 2);
        int d = atoi(t);
        memcpy(t, &cmd_rx_buffer[8], 2);
        int h = atoi(t);
        memcpy(t, &cmd_rx_buffer[10], 2);
        int mi = atoi(t);
        memcpy(t, &cmd_rx_buffer[12], 2);
        int s = atoi(t);
        escrever_rtc(a, m, d, h, mi, s);
    }

    // B: INTERVALO DE AQUISIÇÃO (5 ou 15 min)
    else if (strncmp(cmd_rx_buffer, "CMD:ACQ:", 8) == 0)
    {
    uint16_t novo = atoi(&cmd_rx_buffer[8]);
    // Só aceita valores válidos
    if (novo == 5 || novo == 15) {
        intervalo_aquisicao_min = novo;
    }
    // REGRA: escrita_sd nunca pode ser menor que aquisição
    if (intervalo_escrita_sd_min < intervalo_aquisicao_min) {
        intervalo_escrita_sd_min = intervalo_aquisicao_min;
    }
    // REGRA: lora nunca pode ser menor que escrita_sd
    if (intervalo_lora_min < intervalo_escrita_sd_min) {
        intervalo_lora_min = intervalo_escrita_sd_min;
    }
    atomic_set(&mudanca_pendente_sd, 1);
    atomic_set(&mudanca_pendente_lora, 1);
    }

   // C: INTERVALO ESCRITA SD (30, 60, ou 120 min)
    else if (strncmp(cmd_rx_buffer, "CMD:SD:", 7) == 0)
    {
    uint16_t novo = atoi(&cmd_rx_buffer[7]);
    if (novo == 30 || novo == 60 || novo == 120) {
        // REGRA: SD nunca pode ser menor que aquisição
        if (novo < intervalo_aquisicao_min) novo = intervalo_aquisicao_min;
        intervalo_escrita_sd_min = novo;
    }
    // REGRA: lora nunca pode ser menor que escrita_sd
    if (intervalo_lora_min < intervalo_escrita_sd_min) {
        intervalo_lora_min = intervalo_escrita_sd_min;
    }
    atomic_set(&mudanca_pendente_sd, 1);
    }

    // D: INTERVALO LORAWAN (30, 60, ou 120 min)
    else if (strncmp(cmd_rx_buffer, "CMD:LORA:", 9) == 0)
    {
    uint16_t novo = atoi(&cmd_rx_buffer[9]);
    if (novo == 30 || novo == 60 || novo == 120) {
        // REGRA: LoRa nunca pode ser menor que escrita_sd
        if (novo < intervalo_escrita_sd_min) novo = intervalo_escrita_sd_min;
        intervalo_lora_min = novo;
    }
    atomic_set(&mudanca_pendente_lora, 1);
    }
    
    else if (strncmp(cmd_rx_buffer, "CMD:SAMP:", 9) == 0)
    {
    uint32_t val = (uint32_t)atoi(&cmd_rx_buffer[9]) * 1000;
    if (val < 500) val = 500;
    sampling_interval_ms = val;
    }

    

    // 3. LIBERTAÇÃO DO MUTEX 
    k_mutex_unlock(&config_mutex);

    // 4. REATIVIDADE: Acorda o Brain para aplicar os novos tempos imediatamente
    k_sem_give(&brain_sync_sem);

    printk("[BLE] Comando Processado: %s\n", cmd_rx_buffer);

    return len;
}

BT_GATT_SERVICE_DEFINE(aqualog_svc,
                       BT_GATT_PRIMARY_SERVICE(BT_UUID_AQUALOG_SERVICE),
                       BT_GATT_CHARACTERISTIC(BT_UUID_AQUALOG_ID_CHAR, BT_GATT_CHRC_READ, BT_GATT_PERM_READ, read_id_cb, NULL, NULL),
                       BT_GATT_CHARACTERISTIC(BT_UUID_AQUALOG_DATA_CHAR, BT_GATT_CHRC_NOTIFY, BT_GATT_PERM_READ, NULL, NULL, NULL),
                       BT_GATT_CCC(data_ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
                       BT_GATT_CHARACTERISTIC(BT_UUID_AQUALOG_CONFIG_CHAR, BT_GATT_CHRC_WRITE, BT_GATT_PERM_WRITE, NULL, write_config_cb, NULL));

/* =========================================================================
 * 6. TAREFAS (THREADS)
 * ========================================================================= */

// --- TAREFA MOTOR: Lê Hardware e dita o ritmo das Live Views (BLE) ---
void tarefa_motor_hw_thread(void *p1, void *p2, void *p3)
{
    uint32_t last_p = 0;
    struct LogRecord tr;
    while (1)
    {
        // --- ALTERAÇÃO ESSENCIAL: Só acorda se houver ligação ---
        if (!ble_connected)
        {
            k_sem_take(&ble_data_ready_sem, K_FOREVER);
            // Sincroniza o ponto zero ao ligar
            last_p = (uint32_t)atomic_get(&pulsos_totais_hw);
        }

        k_mutex_lock(&config_mutex, K_FOREVER);
        uint32_t t = sampling_interval_ms;
        k_mutex_unlock(&config_mutex);

        k_sleep(K_MSEC(t));
        if(!ble_connected) {
            continue; // Se durante o sono a ligação caiu, volta a esperar
        }

       uint32_t now_p = (uint32_t)atomic_get(&pulsos_totais_hw);

        // Removemos o set de pulsos_totais_hw porque o Brain agora é independente
        atomic_set(&pulsos_delta_ble, now_p - last_p);
        last_p = now_p;

        if (ble_connected)
        {
            ler_percentagem_bateria_real();
            ler_rtc_para_pacote(&tr);
            k_mutex_lock(&uart_mutex, K_FOREVER);
            printk("[%02d/%02d/%04d %02d:%02d:%02d] P: %u | BAT: %d%% | SD: %dm | LR: %dm| ACQ: %dm\n",
                   tr.day, tr.month, tr.year, tr.hour, tr.min, tr.sec, (uint32_t)atomic_get(&pulsos_delta_ble),
                   bat_level, intervalo_escrita_sd_min, intervalo_lora_min,intervalo_aquisicao_min);
            k_mutex_unlock(&uart_mutex);

            if (notify_enabled)
                k_sem_give(&ble_data_ready_sem);
        }
    }
}

// --- TAREFA BRAIN: Sincronismo Matemático e Gestão de Metadados ---
void tarefa_brain_thread(void *p1, void *p2, void *p3)
{
    uint32_t p_ant_sd = 0, p_ant_lora = 0;
    
    // <-- NOVO: Adicionámos a variável tempo_inicio_lora
    struct LogRecord pac_agora, tempo_inicio_sd, tempo_inicio_lora; 
    
    bool primeira_leitura = true;
    uint32_t int_acq, int_sd, int_lora;
    
    int ultimo_abs_sd = -1;
    int ultimo_abs_lora = -1;
    int ultimo_abs_acq = -1;

    // Array normal de 16-bits (Ainda sem compressão)
    uint16_t array_acumulador_lora[MAX_AMOSTRAS_LORA];
    int idx_lora = 0;

    printk("[Brain] Boot. Logica de Tempos Independentes Ativa (Sem compressao).\n");

    while (1)
    {
        ler_percentagem_bateria_real();
        ler_rtc_para_pacote(&pac_agora);
        uint32_t pulsos_agora = (uint32_t)atomic_get(&pulsos_totais_hw);

        // CÁLCULO CRÍTICO: Horas passadas a minutos absolutos para resolver o bug das 2h!
        uint32_t minutos_absolutos_dia = (pac_agora.hour * 60) + pac_agora.min;

        k_mutex_lock(&config_mutex, K_FOREVER);
        int_acq = intervalo_aquisicao_min;
        int_sd = intervalo_escrita_sd_min;
        int_lora = intervalo_lora_min;
        k_mutex_unlock(&config_mutex);

        if (primeira_leitura) {
            p_ant_sd = p_ant_lora = pulsos_agora;
            tempo_inicio_sd = pac_agora;
            
            // <-- NOVO: Inicializar o tempo de início do LoRa
            tempo_inicio_lora = pac_agora; 
            
            ultimo_abs_acq = ultimo_abs_sd = ultimo_abs_lora = minutos_absolutos_dia;
            memset(array_acumulador_lora, 0, sizeof(array_acumulador_lora));
            primeira_leitura = false;
        }
        else
        {
            bool sd_forcado = atomic_cas(&mudanca_pendente_sd, 1, 0);
            bool lora_forcado = atomic_cas(&mudanca_pendente_lora, 1, 0);

            // --- 1. AQUISIÇÃO (Alimenta o array do LoRa E a fila do SD na RAM) ---
            if ((minutos_absolutos_dia % int_acq == 0 || lora_forcado) && (minutos_absolutos_dia != ultimo_abs_acq))
            {
                ultimo_abs_acq = minutos_absolutos_dia;

                // 1A. Alimenta o Array do LoRa
                if (idx_lora < MAX_AMOSTRAS_LORA) {
                    array_acumulador_lora[idx_lora] = (uint16_t)(pulsos_agora - p_ant_lora);
                    idx_lora++;
                }
                p_ant_lora = pulsos_agora;

                // 1B. Alimenta a RAM do Cartão SD (Cria uma linha)
                struct LogRecord pacote_sd = tempo_inicio_sd;
                pacote_sd.pulses = pulsos_agora - p_ant_sd;
                pacote_sd.config_change = false; 
                pacote_sd.novo_intervalo = int_sd;
                
                k_msgq_put(&sd_msgq, &pacote_sd, K_NO_WAIT); // Fica a aguardar na RAM
                
                p_ant_sd = pulsos_agora;
                tempo_inicio_sd = pac_agora; // O próximo pacote do SD começa a contar a partir de agora
            }

            // --- 2. ESCRITA SD (A cada X min, dá a ordem de gravação em Lote) ---
            if ((minutos_absolutos_dia % int_sd == 0 || sd_forcado) && (minutos_absolutos_dia != ultimo_abs_sd))
            {
                ultimo_abs_sd = minutos_absolutos_dia;
                
                // Se foi forçado por BLE a meio do tempo, guardamos os pulsos que restaram
                if (sd_forcado && (pulsos_agora - p_ant_sd > 0)) {
                    struct LogRecord pacote_extra = tempo_inicio_sd;
                    pacote_extra.pulses = pulsos_agora - p_ant_sd;
                    pacote_extra.config_change = true;
                    pacote_extra.novo_intervalo = int_sd;
                    k_msgq_put(&sd_msgq, &pacote_extra, K_NO_WAIT);
                    p_ant_sd = pulsos_agora;
                    tempo_inicio_sd = pac_agora;
                }

                k_sem_give(&sd_flush_sem); 
                printk("[Brain] Ordem de Flush: Gravar lote no SD.\n");
            }

            // --- 3. ENVIO LORA (Envio do array bruto de 16-bits) ---
            if ((minutos_absolutos_dia % int_lora == 0 || lora_forcado) && (minutos_absolutos_dia != ultimo_abs_lora))
            {
                ultimo_abs_lora = minutos_absolutos_dia;

                struct LoRaPayload pacote_final;
                
                // <-- NOVO: Usar 'tempo_inicio_lora' para construir o cabeçalho em vez de 'pac_agora'
                pacote_final.year_offset = (uint8_t)(tempo_inicio_lora.year - 2000);
                pacote_final.month = tempo_inicio_lora.month;
                pacote_final.day = tempo_inicio_lora.day;
                pacote_final.hour = tempo_inicio_lora.hour;
                pacote_final.min = tempo_inicio_lora.min;
                
                pacote_final.bat_level = (uint8_t)bat_level;
                pacote_final.intervalo_acq_min = (uint8_t)int_acq; 
                
                pacote_final.num_amostras = idx_lora;
                memcpy(pacote_final.pulsos_amostras, array_acumulador_lora, sizeof(uint16_t) * idx_lora);

                if (k_msgq_put(&lora_msgq, &pacote_final, K_NO_WAIT) == 0) {
                    printk("[Brain] LoRa: Balde de %dm enviado. Ref: %02d:%02d\n", 
                           int_acq, tempo_inicio_lora.hour, tempo_inicio_lora.min);
                    
                    idx_lora = 0; 
                    memset(array_acumulador_lora, 0, sizeof(array_acumulador_lora));
                    
                    // <-- NOVO: O próximo ciclo de 120 minutos vai carregar a hora atual como início!
                    tempo_inicio_lora = pac_agora; 
                }
            }
        }

        // --- 4. SONO PRECISO (A placa dorme ao ritmo da AQUISIÇÃO) ---
        struct LogRecord rtc_sync;
        ler_rtc_para_pacote(&rtc_sync);
        uint32_t m_prox = int_acq - (rtc_sync.min % int_acq);
        uint32_t wait_sec = ((m_prox - 1) * 60) + (60 - rtc_sync.sec);

        k_sem_take(&brain_sync_sem, K_MSEC((wait_sec * 1000) + 500));
    }
}
// interrupção do cartão SD: acorda a tarefa SD para tentar montar e gravar os dados
void sd_cd_isr(const struct device *dev, struct gpio_callback *cb, uint32_t pins)
{
    static int64_t last_time = 0;
    int64_t agora = k_uptime_get();
    if (agora - last_time <200 ) return; // Debounce aumentado
    last_time = agora;

    // Lemos o estado físico direto
    int val = gpio_pin_get_dt(&sd_cd_pin);
    
    // Com ACTIVE_LOW: 1 = 0V (Inserido), 0 = 3.3V (Vazio)
    if (val == 1) { 
        printk(">>> [SD] CARTÃO DETETADO (Fisicamente em 0V)\n");
        k_sem_give(&sd_card_inserted_sem);
    } else {
        printk(">>> [SD] CARTÃO REMOVIDO (Fisicamente em 3.3V)\n");
    }
}

// --- TAREFA SD: Escrita física no cartão (Versão Lote + Segurança de Ausência de SD) ---
void tarefa_sd_thread(void *p1, void *p2, void *p3)
{
    const struct device *spi_dev = DEVICE_DT_GET(DT_NODELABEL(spi22));
    char buf[128]; 
    struct LogRecord reg;
    int rc;
    bool mounted = false;

    struct LogRecord buffer_ram[MAX_LOTE_SD]; 
    int falhas_consecutivas = 0;
    
    // VARIÁVEL FORA DO WHILE (Exatamente como tinhas originalmente!)
    // Assim, se o cartão for removido, os dados não se perdem neste buffer.
    int pacotes_acumulados = 0; 

    while (1)
    {
        // 1. DORMIR E RECOLHER DADOS
        if (pacotes_acumulados == 0) {
            // Só vai dormir se a fila de mensagens estiver MESMO vazia
            if (k_msgq_num_used_get(&sd_msgq) == 0) {
                k_sem_take(&sd_flush_sem, K_FOREVER);
            }
            
            while (k_msgq_get(&sd_msgq, &reg, K_NO_WAIT) == 0) {
                buffer_ram[pacotes_acumulados] = reg;
                pacotes_acumulados++;
                if (pacotes_acumulados >= MAX_LOTE_SD) break; // Proteção de memória
            }
        }

        if (pacotes_acumulados == 0) continue; 

        printk("[SD] A tentar gravar %d pacotes de %dmin...\n", pacotes_acumulados, intervalo_aquisicao_min);

        // =======================================================
        // 2. TRANCAR O MUTEX ANTES DE TOCAR NO HARDWARE!
        // =======================================================
        k_mutex_lock(&power_lock_mutex, K_FOREVER);

        if (device_is_ready(spi_dev)) {
            npm1300_vout1_ligar(); 
            k_msleep(150); 
            if (gpio_is_ready_dt(&sd_cs_pin)) gpio_pin_configure_dt(&sd_cs_pin, GPIO_OUTPUT_INACTIVE);
            pm_device_action_run(spi_dev, PM_DEVICE_ACTION_RESUME);
        }

        // DETEÇÃO DO SLOT VAZIO (A TUA SEGURANÇA)
        if (gpio_pin_get_dt(&sd_cd_pin) == 0)
        {
            if (mounted) {
                fs_unmount(&mp);
                disk_access_ioctl("SD", DISK_IOCTL_CTRL_DEINIT, NULL);
                mounted = false;
            }
            printk("[SD] Cartão removido! Retendo dados na RAM...\n");
            
            if (device_is_ready(spi_dev)) {
                pm_device_action_run(spi_dev, PM_DEVICE_ACTION_SUSPEND);
                if (gpio_is_ready_dt(&sd_cs_pin)) gpio_pin_configure_dt(&sd_cs_pin, GPIO_DISCONNECTED);
                npm1300_vout1_desligar();
            }
            k_mutex_unlock(&power_lock_mutex); // <-- DESTRANCAR!
            
            // Fica a dormir à espera que voltes a inserir o cartão
            k_sem_take(&sd_card_inserted_sem, K_FOREVER);
            
            // Faz continue, mas como pacotes_acumulados > 0, ele tenta gravar logo sem perder dados!
            continue;
        }

        // MONTAGEM DO CARTÃO 
        if (!mounted)
        {
            disk_access_ioctl("SD", DISK_IOCTL_CTRL_DEINIT, NULL);
            k_sleep(K_MSEC(100));

            if (disk_access_init("SD") != 0 || fs_mount(&mp) != 0)
            {
                falhas_consecutivas++;
                printk("[SD] Erro a Montar CMD0 (Tentativa %d/3).\n", falhas_consecutivas);
                disk_access_ioctl("SD", DISK_IOCTL_CTRL_DEINIT, NULL);
                
                if (device_is_ready(spi_dev)) {
                    pm_device_action_run(spi_dev, PM_DEVICE_ACTION_SUSPEND);
                    if (gpio_is_ready_dt(&sd_cs_pin)) gpio_pin_configure_dt(&sd_cs_pin, GPIO_DISCONNECTED);
                    npm1300_vout1_desligar();
                }
                
                if (falhas_consecutivas >= 3) {
                    printk("[SD] FATAL: SD danificado. A descartar dados na RAM para evitar encravar o sistema.\n");
                    pacotes_acumulados = 0; // Só deitamos dados fora ao fim de 3 tentativas falhadas com o cartão lá dentro
                    falhas_consecutivas = 0;
                } else {
                    k_msleep(2000); 
                }
                
                k_mutex_unlock(&power_lock_mutex); // <-- DESTRANCAR!
                continue;
            }
            mounted = true;
            falhas_consecutivas = 0; 
        }

        // INÍCIO DA ESCRITA
        rc = fs_open(&log_file, LOG_FILE_PATH, FS_O_WRITE | FS_O_CREATE | FS_O_APPEND);
        if (rc == 0)
        {
            bool erro_escrita = false;
            
            // FAZ UM LOOP PELO LOTE TODO E GRAVA TODAS AS LINHAS
            for (int i = 0; i < pacotes_acumulados; i++) {
                if (buffer_ram[i].config_change) {
                    char header[64];
                    snprintf(header, sizeof(header), "\n# --- MUDANCA DE INTERVALO SD PARA: %u MIN ---\n", buffer_ram[i].novo_intervalo);
                    fs_write(&log_file, header, strlen(header));
                }
                snprintf(buf, sizeof(buf), "%04d/%02d/%02d,%02d:%02d:%02d,%u\n",
                         buffer_ram[i].year, buffer_ram[i].month, buffer_ram[i].day, 
                         buffer_ram[i].hour, buffer_ram[i].min, buffer_ram[i].sec, 
                         buffer_ram[i].pulses);

                if (fs_write(&log_file, buf, strlen(buf)) < 0) {
                    erro_escrita = true;
                    break; 
                }
            }
            fs_close(&log_file);

            if (erro_escrita) {
                printk("[SD] Erro a meio da escrita! Falha.\n");
                fs_unmount(&mp);
                disk_access_ioctl("SD", DISK_IOCTL_CTRL_DEINIT, NULL);
                mounted = false;
                
                if (device_is_ready(spi_dev)) {
                    pm_device_action_run(spi_dev, PM_DEVICE_ACTION_SUSPEND);
                    if (gpio_is_ready_dt(&sd_cs_pin)) gpio_pin_configure_dt(&sd_cs_pin, GPIO_DISCONNECTED);
                    npm1300_vout1_desligar();
                }
                k_msleep(100); 
            } else {
                printk("[SD] SUCESSO: Gravado! Buffer limpo.\n");
                
                // Dados gravados com sucesso, esvazia o buffer!
                pacotes_acumulados = 0; 

                fs_unmount(&mp);
                disk_access_ioctl("SD", DISK_IOCTL_CTRL_DEINIT, NULL);
                mounted = false;

                if (device_is_ready(spi_dev)){
                    pm_device_action_run(spi_dev, PM_DEVICE_ACTION_SUSPEND);
                    if (gpio_is_ready_dt(&sd_cs_pin)) {
                        gpio_pin_configure_dt(&sd_cs_pin, GPIO_DISCONNECTED);
                    }
                    npm1300_vout1_desligar(); 
                }
            }
        }
        else
        {
            printk("[SD] Erro a abrir ficheiro.\n");
            fs_unmount(&mp);
            disk_access_ioctl("SD", DISK_IOCTL_CTRL_DEINIT, NULL);
            mounted = false;
            
            if (device_is_ready(spi_dev)) {
                pm_device_action_run(spi_dev, PM_DEVICE_ACTION_SUSPEND);
                if (gpio_is_ready_dt(&sd_cs_pin)) {
                    gpio_pin_configure_dt(&sd_cs_pin, GPIO_DISCONNECTED);
                }
                npm1300_vout1_desligar();
            }
            k_msleep(100);
        }

        // =======================================================
        // 3. COM O HARDWARE A DORMIR, DESTRANCAR O MUTEX!
        // =======================================================
        k_mutex_unlock(&power_lock_mutex);
    }
}
// Define o nome e as flags que aparecem no Scan do telemóvel
// O compilador vai substituir 'CONFIG_BT_DEVICE_NAME' pelo texto "Aqualog_nRF54_V2"
static const struct bt_data ad[] = {
    BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
    BT_DATA(BT_DATA_NAME_COMPLETE, CONFIG_BT_DEVICE_NAME, sizeof(CONFIG_BT_DEVICE_NAME) - 1),
};

// --- TAREFA BLE: Gestão Completa do Rádio, Conexões e Comunicação ---
void tarefa_ble_thread(void *p1, void *p2, void *p3)
{
    bt_enable(NULL);
    bt_conn_cb_register(&conn_callbacks);
    struct bt_le_ext_adv *adv;
    struct bt_le_adv_param param = BT_LE_ADV_PARAM_INIT(
        BT_LE_ADV_OPT_CONN | BT_LE_ADV_OPT_USE_NAME, 
        BT_GAP_ADV_SLOW_INT_MIN,
        BT_GAP_ADV_SLOW_INT_MAX,
        NULL);
        
    bt_le_ext_adv_create(&param, NULL, &adv);
    bt_le_ext_adv_set_data(adv, ad, ARRAY_SIZE(ad), NULL, 0);
    while (1)
    {
        // Estado: Rádio totalmente desligado, à espera do Botão 3
        k_sem_take(&ble_start_adv_sem, K_FOREVER);

        printk("[BLE] Botao pressionado. A iniciar rádio...\n");
        bt_le_ext_adv_start(adv, BT_LE_EXT_ADV_START_DEFAULT);
        ble_advertising = true;
        //gpio_pin_set_dt(&led_ble, 1); apenas para debug, o LED é controlado pela thread BLE

        // Aguarda conexão ou timeout de 30s
        for (int i = 0; i < 30 && !ble_connected; i++)
        {
            k_sleep(K_SECONDS(1));
        }

        if (!ble_connected)
        {
            bt_le_ext_adv_stop(adv);
            ble_advertising = false;
            //gpio_pin_set_dt(&led_ble, 0); apenas para debug, o LED é controlado pela thread BLE
            printk("[BLE] Ninguém ligou. Rádio OFF para poupar bateria.\n");
            continue; // Volta para o k_sem_take
        }

        // --- ESTÁ CONECTADO ---
        while (ble_connected)
        {
            if (k_sem_take(&ble_data_ready_sem, K_MSEC(500)) == 0)
            {
                if (notify_enabled)
                {
                    snprintf(data_tx_buffer, sizeof(data_tx_buffer), "{\"b\":%d,\"l\":%d}",
                             bat_level, (int)atomic_get(&pulsos_delta_ble));
                    bt_gatt_notify(NULL, &aqualog_svc.attrs[4], data_tx_buffer, strlen(data_tx_buffer));
                }
            }
        }

        // --- ACABOU DE DESCONECTAR ---
        bt_le_ext_adv_stop(adv); // Garante que o rádio para de emitir sinal
        ble_advertising = false;
        //gpio_pin_set_dt(&led_ble, 0); apenas para debug, o LED é controlado pela thread BLE
        printk("[BLE] Desconectado. Rádio encerrado totalmente.\n");

        // O loop volta ao início e fica parado no k_sem_take à espera do botão 3
    }
}

/* INTERRUPÇÃO BOTÃO 3: Apenas dá o sinal à thread BLE */
void button3_isr(const struct device *dev, struct gpio_callback *cb, uint32_t pins)
{
    k_sem_give(&ble_start_adv_sem);
}

// --- TAREFA LORA: Envio de Dados Formato SD para a Nuvem (TTN) ---
void tarefa_lora_thread(void *p1, void *p2, void *p3)
{
    const struct device *lora_dev = DEVICE_DT_GET(DT_ALIAS(lora0));
    const struct device *spi_dev = DEVICE_DT_GET(DT_NODELABEL(spi22));
    
    struct LoRaPayload payload_recebido;
    int ret;
    
    bool lora_joined = false; 
    bool lora_started = false;

    if (!device_is_ready(lora_dev) || !device_is_ready(spi_dev)) {
        printk("[LoRaWAN] Erro: Hardware RFM95 ou SPI não pronto.\n");
        return;
    }

    struct lorawan_join_config join_cfg = {0};
    join_cfg.mode = LORAWAN_ACT_OTAA;
    join_cfg.dev_eui = dev_eui;
    join_cfg.otaa.join_eui = join_eui;
    join_cfg.otaa.app_key = app_key;
    join_cfg.otaa.nwk_key = app_key;

    printk("[LoRaWAN] Thread pronta. Rádio em Deep Sleep até haver dados...\n");

    while (1)
    {
        if (k_msgq_get(&lora_msgq, &payload_recebido, K_FOREVER) == 0)
        {
            // =======================================================
            // 1. TRANCAR O MUTEX ANTES DE TOCAR NO HARDWARE!
            // =======================================================
            k_mutex_lock(&power_lock_mutex, K_FOREVER);
            // A SOLUÇÃO: Ligar o SD temporariamente para não asfixiar o barramento SPI!
            npm1300_vout1_ligar();
            k_msleep(150); // Dar tempo para a energia estabilizar
            
            // Colocar o Chip Select do SD a ALTO para ele ignorar a conversa do LoRa
            if (gpio_is_ready_dt(&sd_cs_pin)) {
                gpio_pin_configure_dt(&sd_cs_pin, GPIO_OUTPUT_INACTIVE);
            }

            pm_device_action_run(spi_dev, PM_DEVICE_ACTION_RESUME);
            pm_device_action_run(lora_dev, PM_DEVICE_ACTION_RESUME);

            if (!lora_started) 
            {
                printk("[LoRaWAN] A arrancar motor do rádio...\n");
                ret = lorawan_start();
                if (ret < 0) {
                    printk("[LoRaWAN] Erro ao iniciar stack: %d\n", ret);
                    goto lora_suspend;
                }
                lorawan_enable_adr(true);
                lorawan_set_class(LORAWAN_CLASS_A);
                settings_load();
                lora_started = true;
            }

            // FASE DE JOIN (FAIL-FAST: Largar e dormir se não houver rede)
            if (!lora_joined) 
            {
                printk("[LoRaWAN] Temos dados! A tentar Join no TTN...\n");
                ret = lorawan_join(&join_cfg);
                if (ret < 0) {
                printk("[LoRaWAN] Join falhou com erro: %d. A abortar!\n", ret);}
                if (ret < 0) {
                    printk("[LoRaWAN] Join falhou. A abortar envio para poupar bateria!\n");
                    goto lora_suspend;
                } else {
                    printk("[LoRaWAN] SUCESSO! Ligado ao TTN.\n");
                    lora_joined = true;  
                }
            }

            // FASE DE ENVIO
            // 7B header + ceil(24/2)*3 = 43 bytes máximo
            uint8_t bin_payload[43];
            int payload_len = encode_payload_lora(&payload_recebido,
                                                bin_payload,
                                                sizeof(bin_payload));
            if (payload_len < 0) {
                printk("[LoRaWAN] Erro: Falha no encoding.\n");
                goto lora_suspend;
            }

            printk("[LoRaWAN] Enviando %dB (%d amostras de %dmin) | BAT:%d%%\n",
                payload_len,
                payload_recebido.num_amostras,
                payload_recebido.intervalo_acq_min,
                payload_recebido.bat_level);

            ret = lorawan_send(1, bin_payload, (uint8_t)payload_len,
                            LORAWAN_MSG_UNCONFIRMED);

            if (ret < 0) printk("[LoRaWAN] Erro envio: %d\n", ret);
            else         printk("[LoRaWAN] OK! %dB enviados.\n", payload_len);

            // TAREFA FEITA: ADORMECER HARDWARE
            lora_suspend:
            pm_device_action_run(lora_dev, PM_DEVICE_ACTION_SUSPEND);
            pm_device_action_run(spi_dev, PM_DEVICE_ACTION_SUSPEND);
            // A SOLUÇÃO: Desligar o pino CS e matar o VOUT1 outra vez
            if (gpio_is_ready_dt(&sd_cs_pin)) {
                gpio_pin_configure_dt(&sd_cs_pin, GPIO_DISCONNECTED);
            }
            npm1300_vout1_desligar(); // SD volta a dormir a 0 µA
            printk("[LoRaWAN] Rádio suspenso. A aguardar novos dados.\n");

            // =======================================================
            // 2. COM O HARDWARE A DORMIR, DESTRANCAR O MUTEX!
            // =======================================================
            k_mutex_unlock(&power_lock_mutex);
        }
    }
}


/* Inicialização das Threads */
K_THREAD_DEFINE(motor_id, STACK_MOTOR, tarefa_motor_hw_thread, NULL, NULL, NULL, PRIO_MOTOR, 0, 0);
K_THREAD_DEFINE(brain_id, STACK_BRAIN, tarefa_brain_thread, NULL, NULL, NULL, PRIO_BRAIN, 0, 0);
K_THREAD_DEFINE(sd_id, STACK_SD, tarefa_sd_thread, NULL, NULL, NULL, PRIO_SD, 0, 0);
K_THREAD_DEFINE(ble_id, STACK_BLE, tarefa_ble_thread, NULL, NULL, NULL, PRIO_BLE, 0, 0);
K_THREAD_DEFINE(lora_id, STACK_LORA, tarefa_lora_thread, NULL, NULL, NULL, PRIO_LORA, 0, 0);


/* =========================================================================
 * 7. PONTO DE ENTRADA (MAIN)
 * ========================================================================= */
int main(void)
{
    printk("\n### AQUALOOG PRO nRF54L15: SISTEMA INTEGRAL ATIVO ###\n");
    
    // TEMPO PARA ESTABILIZAR VOLTAGENS ANTES DE CORTES DRÁSTICOS
    k_msleep(100);

    npm1300_vout1_desligar(); // Garante que o BUCK1 está morto
    // 1. MATAR O RÁDIO LORA 
    const struct device *lora_dev = DEVICE_DT_GET(DT_ALIAS(lora0));
    if (device_is_ready(lora_dev)) {
        pm_device_action_run(lora_dev, PM_DEVICE_ACTION_SUSPEND);
    }

    if (gpio_is_ready_dt(&sd_cs_pin)) {
        // Desliga o pino CS fisicamente para o cartão não roubar energia
        gpio_pin_configure_dt(&sd_cs_pin, GPIO_DISCONNECTED); 
    }

    // 2. MATAR O BARRAMENTO SPI (Corta os 150 µA internos)
    const struct device *spi_dev = DEVICE_DT_GET(DT_NODELABEL(spi22));
    if (device_is_ready(spi_dev)) {
        pm_device_action_run(spi_dev, PM_DEVICE_ACTION_SUSPEND);
    }
    // MATAR O BARRAMENTO I2C NO ARRANQUE 
    const struct device *i2c_dev = DEVICE_DT_GET(DT_NODELABEL(i2c21));
    if (device_is_ready(i2c_dev)) {
        pm_device_action_run(i2c_dev, PM_DEVICE_ACTION_SUSPEND);
    }
     
    // 3. CONFIGURAÇÃO DE PINOS COM PULL-UP (Para evitar "antenas" e interrupções falsas)
    
    if (gpio_is_ready_dt(&sd_cd_pin)) {
        
        gpio_pin_configure_dt(&sd_cd_pin, GPIO_INPUT);
        
        gpio_pin_interrupt_configure_dt(&sd_cd_pin, GPIO_INT_EDGE_BOTH);
        gpio_init_callback(&sd_cd_cb_data, sd_cd_isr, BIT(sd_cd_pin.pin));
        gpio_add_callback(sd_cd_pin.port, &sd_cd_cb_data);
        if (gpio_pin_get_dt(&sd_cd_pin) == 1) k_sem_give(&sd_card_inserted_sem);
    }
    
   
    if (gpio_is_ready_dt(&pino_agua)) {
        gpio_pin_configure_dt(&pino_agua, GPIO_INPUT);
        gpio_pin_interrupt_configure_dt(&pino_agua, GPIO_INT_EDGE_TO_ACTIVE);
        gpio_init_callback(&agua_cb_data, pino_agua_isr, BIT(pino_agua.pin));
        gpio_add_callback(pino_agua.port, &agua_cb_data);
    }

    if (gpio_is_ready_dt(&button3)) {
        gpio_pin_configure_dt(&button3, GPIO_INPUT | GPIO_PULL_UP);
        gpio_pin_interrupt_configure_dt(&button3, GPIO_INT_EDGE_TO_ACTIVE);
        gpio_init_callback(&button3_cb, button3_isr, BIT(button3.pin));
        gpio_add_callback(button3.port, &button3_cb);
    }

    // O Sistema vai agora afundar nos microamperes a aguardar botões ou dados
    while (1) {
   
        k_sleep(K_FOREVER);

    }
    return 0;
}