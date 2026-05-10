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
*   **Multicall:** Wszystkie boty Aave używają Multicall3 do błyskawicznego pobierania danych (oszczędność RPC).
*   **Python Venv:** Zawsze upewnij się, że masz aktywny venv: `.\.venv\Scripts\activate`.

---

## 🧪 Testowanie Konfiguracji (Self-Test)
Jeśli chcesz mieć pewność, że Twoje klucze, RPC i kontrakty są poprawnie skonfigurowane, uruchom skrypty testowe:

*   **Arbitrum:** `python test_executor_arbitrum.py`
*   **Plasma:** `python test_executor_plasma.py`

**Spodziewany wynik (SUKCES):**
Jeśli zobaczysz komunikat `Position is healthy, HF >= 1`, oznacza to, że:
1.  Skrypt poprawnie podpisał transakcję Twoim `PRIVATE_KEY`.
2.  Kontrakt na blockchainie odebrał zapytanie i skontaktował się z AAVE.
3.  Zostałeś rozpoznany jako właściciel (`owner`) kontraktu.

---
*Ostatnia aktualizacja: 2026-05-10*
