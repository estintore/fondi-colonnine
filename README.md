# Fondi Bonus colonnine domestiche

Ogni sera un workflow GitHub Actions apre la dashboard Power BI di Invitalia,
legge i fondi ancora disponibili e aggiunge una riga a `data.csv`.
La pagina `index.html`, pubblicata con GitHub Pages, mostra grafici e previsione di esaurimento.

## Messa in funzione (5 minuti)

1. Crea un nuovo repository su GitHub (pubblico, così Actions e Pages sono gratuiti) e carica tutti i file di questa cartella, compresa la cartella nascosta `.github`.
2. **Settings → Actions → General → Workflow permissions**: scegli *Read and write permissions* e salva.
3. **Settings → Pages**: in *Source* scegli *Deploy from a branch*, branch `main`, cartella `/ (root)`, e salva. Dopo un minuto la dashboard sarà su `https://<tuo-utente>.github.io/<nome-repo>/`.
4. **Actions → Rileva fondi colonnine → Run workflow** per fare subito la prima lettura.
5. Apri l'esecuzione e controlla il riepilogo: deve riportare l'importo corretto. Nella sezione *Artifacts* trovi `debug` con lo screenshot della dashboard e tutti gli importi trovati.

Da lì in poi gira da solo ogni giorno alle 20:00 UTC (22:00 ora legale, 21:00 ora solare).

## Se l'importo letto non è quello giusto

La dashboard può mostrare più importi (dotazione, prenotato, disponibile). Lo script sceglie quello vicino
alla parola «disponibil»; se non la trova, prende l'importo più vicino al valore del giorno prima.
Se prende quello sbagliato:

- scarica l'artifact `debug` e apri `candidates.json`: ogni importo ha il testo che lo circonda;
- trova la parola che precede l'importo giusto (per esempio «residu») e impostala come variabile:
  **Settings → Secrets and variables → Actions → Variables → New variable**, nome `KEYWORD`. Il workflow la usa già.

Se Invitalia cambia il link della dashboard, fai lo stesso con `REPORT_URL`.

## Note

- Puoi correggere o aggiungere valori a mano modificando `data.csv` direttamente su GitHub (`data;importo`, una riga per giorno).
- GitHub a volte ritarda i workflow programmati anche di qualche decina di minuti: è normale.
- `data.csv` si può importare anche nell'app su Claude con il pulsante «Importa CSV».
