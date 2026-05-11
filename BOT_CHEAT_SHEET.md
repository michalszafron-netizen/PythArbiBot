# 🤖 Liquidation Bots Cheat Sheet (Multi-Network)

Ten folder zawiera 3 niezależne systemy likwidacji. Poniżej znajdziesz instrukcję obsługi każdego z nich.

---

## 1. AAVE V3 - Arbitrum (Aktywny ✅)
Bot wykorzystujący **Pyth Early Warning** (off-chain prices) dla przewagi nad konkurencją.
*   **Plik główny:** `aave_main.py`
*   **Komendy:**
    *   `python aave_main.py --live` – Uruchomienie w trybie rzeczywistym (wysyła transakcje!).
    *   `python aave_main.py` – Domyślnie tryb **Dry-Run** (tylko symulacja).
    *   `python aave_main.py --scan-interval 30` – Zmiana szybkości skanowania (domyślnie 60s).
*   **Logi:** `arbitrum_bot.log`
*   **Konfiguracja:** `aave_config.py` (Adresy, Feedy Pyth, Progi HF).
*   **Zmiana progów HF:** Edytuj `aave_config.py`:
    *   `HF_LIQUIDATABLE = 1.0`
    *   `HF_MONITOR = 1.2`

---

## 2. AAVE V3 - Plasma (Aktywny ✅)
Specjalny bot na sieć Plasma (re.al), który sam odkrywa dłużników skanując logi (brak subgraphu).
*   **Plik główny:** `plasma_main.py`
*   **Komendy:**
    *   `python plasma_main.py` – Uruchomienie bota (tryb live zależny od adresu egzekutora w `.env`).
*   **Logi:** `plasma_bot.log`
*   **Baza dłużników:** `plasma_borrowers.json` (Plik aktualizowany automatycznie).
*   **Ostatni blok:** `plasma_last_block.txt` – Tu bot zapisuje postęp skanowania, żeby nie zaczynać od zera po restarcie.
*   **Parametry skanowania:** W `plasma_main.py` możesz zmienić `batch_size` (paczka bloków) lub `time.sleep` (oddech dla RPC).

---

## 3. GMX V2 - Arbitrum (Nieaktywny ⏳)
Bot monitorujący pozycje na GMX Synthetics.
*   **Plik główny:** `main.py` (wymaga `gmx_positions.py`).
*   **Status:** Wymaga roli `LIQUIDATION_KEEPER` na kontrakcie GMX, aby móc egzekwować likwidacje.
*   **Komendy:**
    *   `python main.py --snapshot-interval 120` – Skanowanie pozycji GMX co 2 minuty.
*   **Logi:** `execution.log`
*   **Konfiguracja:** `config.py` (Dla GMX).

---

## 📊 Statusy i Oznaczenia (Aave)
W tabelach konsolowych zobaczysz następujące statusy:
*   `!!! LIKWIDACJA !!!` (HF < 1.0) – Bot próbuje wysłać transakcję.
*   `KRYTYCZNY` (HF < 1.05) – Pozycja pod ścisłym nadzorem Pyth Early Warning.
*   `ZAGROŻONY` (HF < 1.20) – Widoczny w tabeli monitorowania.
*   `OK` (HF > 1.20) – Bezpieczna pozycja.

---

## ⚡ Rozwiązywanie Problemów i Brak Akcji
Jeśli bot wykonuje tysiące skanów (`SCANS`) i nic się nie dzieje:

1.  **Brak zmienności (Volatility):** To najczęstsza przyczyna. Likwidacje zdarzają się przy gwałtownych ruchach (2-5% w kilka minut). Jeśli rynek stoi, HF dłużników nie drgnie.
2.  **Duże Delty (Early Warning):** Jeśli widzisz np. `Δ = -8.00%` przez dłuższy czas, a bot nie likwiduje:
    *   Może to być asset z "LST" (np. weETH, wstETH), gdzie Aave używa specjalnej wyceny (np. Ratio do ETH), a Pyth pokazuje czystą cenę rynkową.
    *   To normalne — bot czeka, aż **on-chain Health Factor** spadnie poniżej 1.0.
3.  **Sprawdzenie "czy żyje":**
    *   Spójrz na kolumnę `HF` w tabeli. Jeśli cyfry po przecinku się zmieniają (np. z 1.0023 na 1.0024), to znaczy, że bot poprawnie pobiera dane on-chain i Multicall działa.
    *   Sprawdź `arbitrum_bot.log` lub `plasma_bot.log` pod kątem błędów `[ERROR]`. Brak błędów = bot jest gotowy do strzału.

## 🛠️ Wspólne Elementy
*   **Plik `.env`:** Tu trzymasz `PRIVATE_KEY` oraz adresy egzekutorów dla obu sieci.
*   **Multicall v3**: Błyskawiczne sprawdzanie tysięcy portfeli w jednej transakcji.
*   **Dystans do likwidacji**: Wyliczany w czasie rzeczywistym dla każdego typu pozycji.
*   **Dynamic Turbo Mode**: Automatyczne przyspieszenie skanu do 5s, gdy wykryto zagrożenie.
*   **Python Venv**: Zawsze upewnij się, że masz aktywny venv: `.\.venv\Scripts\activate`.

---

## 🧪 Testowanie

## 📊 Jak czytać Panel Intelligence (v2.0)

Nowy dashboard dostarcza danych analitycznych w czasie rzeczywistym:

| Kolumna | Znaczenie | Dlaczego to ważne? |
| :--- | :--- | :--- |
| **TYP** | LONG, SHORT, LOOP | Mówi nam, na co gra użytkownik. |
| **DYSTANS** | % do likwidacji | Pokazuje "zapas" bezpieczeństwa. Jeśli widzisz < 1%, bądź gotowy! |
| **CENA LIQ** | Cena punktu zero | Konkretna cena aktywa, przy której pozycja wybucha. |
| **AKTYWA** | Collateral / Debt | Widzisz np. `weETH/WETH`. To jest Twój cel przy depegu. |

### 🔍 Strategia "Depeg / Loop"
Większość pozycji na Arbitrum to `weETH/WETH` (LOOP).
- **Zasada**: Użytkownik liczy na staking yield, my liczymy na to, że `weETH` spadnie względem `WETH` o te ułamki procenta.
- **Kiedy to zadziała?**: Podczas gwałtownych ruchów ETH, kiedy płynność weETH maleje.

---

## 🚀 Komendy START (Tryb Produkcyjny)

Zawsze zaczynaj od `dry-run`, a gdy rynek zacznie się ruszać, przełącz na `--live`.

### Arbitrum One
```powershell
# Tryb podglądu (Bezpieczny)
python aave_main.py --dry-run --scan-interval 30

# Tryb LIVE (Realne transakcje)
python aave_main.py --live --scan-interval 15
```

### Plasma (wkrótce)
*Obecnie testujemy dashboard na Arbitrum. Po potwierdzeniu stabilności, wdrożymy te same metryki dla Plazmy.*

---

## 🛠️ Szybka diagnostyka przed LIVE
Zanim odpalsz tryb `--live`, wykonaj te dwa testy:

1. **Test kontraktu (Arbitrum)**: `python test_executor_arbitrum.py`
   - Powinieneś zobaczyć błąd `HF > 1` – to znak, że połączenie z kontraktem działa!
2. **Test analizy**: `python aave_positions.py`
   - Sprawdź, czy widzisz tabelkę z "Intelligence".

*Ostatnia aktualizacja: 2026-05-10*
