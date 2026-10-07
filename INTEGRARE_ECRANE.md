# Integrare ecran cu serverul „Configurare Magazin"

Până acum configurația fiecărui ecran (ce game afișează, titluri, lățimi) era setată local, pe dispozitiv.
Acum ea este definită **pe server**, iar dispozitivul doar o citește și o afișează. Dispozitivul nu mai decide singur ce afișează.

Serverul este o aplicație Flask pe rețeaua locală. Implicit ascultă pe `0.0.0.0:18766`, adică pe toate interfețele de rețea, deci dispozitivele din rețeaua locală îl pot accesa (adresa și portul se schimbă cu `APP_HOST` și `APP_PORT`; `APP_HOST=127.0.0.1` îl limitează la calculatorul pe care rulează). Portul trebuie scris mereu în adresă, ex. `http://192.168.1.10:18766`. Nu există autentificare. Toate răspunsurile sunt JSON, UTF-8.

## Ce păstrează dispozitivul local

1. **Adresa serverului**, cu portul inclus, ex. `http://192.168.1.10:18766` (portul implicit; dacă serverul rulează pe alt port, folosește-l pe acela). Este setarea locală, singura rămasă obligatorie.
2. **Identificatorul ecranului** (număr întreg, ex. `7`), primit de la server. Trebuie salvat **pe disc** și să supraviețuiască repornirii.
3. Ultimul răspuns valid de la server (pentru situația în care serverul nu răspunde).

## Fluxul dispozitivului

1. La pornire, dacă **nu are** identificator salvat: `POST {server}/api/screens/register` (fără corp). Răspuns `201`: `{"id": 7}`.
   **Salvează imediat id-ul pe disc.** Apelează `register` o singură dată; fiecare apel creează un ecran nou pe server.
2. Dacă are identificator: sare direct la pasul 3.
3. Cere configurația: `GET {server}/json_screen/{id}`. Repetă la fiecare `refresh_seconds` (valoare primită în răspuns, se poate schimba oricând din aplicație, deci o citești din **fiecare** răspuns).
4. Afișează ce primește (vezi mai jos). La fiecare răspuns nou, redesenează.

Serverul reține și **adresa IP** de pe care vine cererea (la `register` și la fiecare `GET /json_screen/{id}`) și o arată în aplicație, în „Configurare ecrane". Dispozitivul nu trebuie să trimită nimic în plus.

Fiecare `GET /json_screen/{id}` este și un semnal că ecranul e activ: aplicația afișează „ultima conexiune" și un indicator verde dacă a venit un răspuns în ultimele ~3 intervale. Nu cere configurația mai rar decât `refresh_seconds`.

## Răspunsul `GET /json_screen/{id}`

```json
{
  "id": 1,
  "name": "Raft fructe",
  "configured": true,
  "layout": "2+4",
  "refresh_seconds": 60,
  "blocks": [
    {
      "title": "Fructe proaspete",
      "gama": "FRUCTE",
      "width": 2,
      "items": [
        {"barcode": "5948792035938", "name": "ARO MASLINE INT LIGHT 400G", "um": "BUC", "price": 12.5, "quantity": 7.0}
      ]
    },
    {
      "title": "Panificatie",
      "gama": "PAINE",
      "width": 4,
      "items": [
        {"barcode": "5900000000001", "name": "Paine alba", "um": "BUC", "price": 8.5, "quantity": 6.0}
      ]
    }
  ]
}
```

- `configured`: `false` înseamnă că ecranul tocmai s-a înregistrat și nu are încă nimic setat în aplicație. Atunci `blocks` este `[]` și `layout` este `null`.
- `refresh_seconds`: la câte secunde ceri din nou configurația (între 10 și 3600).
- `blocks`: **1 sau 2 blocuri**, în ordinea de la stânga la dreapta.
  - `title`: textul afișat deasupra blocului.
  - `width`: lățimea blocului în coloane dintr-o grilă de **6**. Blocurile unui ecran dau mereu împreună 6. Combinațiile posibile: `6`, `3+3`, `2+4`, `4+2`. Folosește direct `width`-ul fiecărui bloc, nu e nevoie să interpretezi `layout`.
  - `gama`: identificator intern. Nu îl afișa și nu depinde de el, afișezi `title`.
  - `items`: produsele blocului, deja sortate alfabetic după nume. Poate fi listă goală (gamă fără produse sau toate produsele fără stoc).
- Un produs are: `barcode`, `name`, `um` (poate fi `""`), `price` (număr, ex. `12.5`; afișează cu 2 zecimale: `12.50`), `quantity` (stocul, număr). `price` și `quantity` pot fi, rar, `null`: tratează-le ca „necunoscut", fără să crape.

## Ce face deja serverul (nu reimplementa pe dispozitiv)

- **Produsele cu stoc exact 0 nu mai sunt trimise.** Nu filtra stocul pe dispozitiv și nu afișa mesaje de tip „stoc epuizat".
- Alegerea produselor din gamă (inclusiv cele adăugate manual) se face pe server.
- Ordinea blocurilor, titlurile și lățimile vin gata făcute.

## Ecran neconfigurat

Dacă `configured` este `false`, afișează clar **identificatorul ecranului** (ex. „ID 7") și un mesaj de tip „Ecran neconfigurat". Cel care îl configurează caută acest număr în aplicație, în „Configurare ecrane". Continuă să ceri configurația la `refresh_seconds`: când e configurat, ecranul trebuie să treacă singur la afișarea produselor, fără repornire.

## Erori și cazuri limită

- **Serverul nu răspunde** (timeout, conexiune refuzată, eroare 5xx):
  - dacă ai deja un răspuns bun, **continuă să-l afișezi** și încearcă din nou la următorul interval;
  - dacă ești la primul start și nu ai id, reîncearcă `register` periodic (nu afișa ecran gol fără explicație);
  - folosește timeout scurt la cereri (5–10 secunde).
- **`404` la `GET /json_screen/{id}`**: ecranul a fost șters din aplicație sau id-ul e greșit (`{"error": "Ecran negăsit"}`). Nu te reînregistra automat în buclă. Afișează un mesaj cu id-ul și lasă utilizatorul să aleagă: introduce alt id sau înregistrează un ecran nou.
- **Răspuns invalid** (JSON stricat, câmpuri lipsă): ignoră-l și păstrează ultimul răspuns bun.

## Introducerea manuală a identificatorului pe ecran

Utilizatorul trebuie să poată **introduce/schimba identificatorul direct pe dispozitiv**. Așa înlocuiește un dispozitiv defect: scrie pe cel nou id-ul vechi și acesta preia configurația ecranului. După schimbare, salvează noul id pe disc și cere imediat configurația.

## Compatibilitate (vechi)

`GET {server}/json_items?gama=<valoare>` funcționează în continuare, dar fără configurație (fără titluri, lățimi, aranjament). Se întoarce `{"<valoare>": [produse]}`, fără produsele cu stoc exact 0. Odată trecut la `/json_screen`, dispozitivul nu ar mai trebui să-l folosească.

## Cum se testează (de pe un calculator din rețea)

```
curl -X POST http://SERVER:18766/api/screens/register
curl http://SERVER:18766/json_screen/1
curl http://SERVER:18766/json_screen/99999     # trebuie să dea 404
```

Verificări: (1) la prima pornire primește un id și îl păstrează după repornire; (2) neconfigurat arată „ID n"; (3) după configurare în aplicație, apare singur după cel mult `refresh_seconds`; (4) cu serverul oprit continuă să afișeze ultimele produse; (5) un produs cu stoc 0 din gamă nu apare pe ecran; (6) schimbarea id-ului din ecran face dispozitivul să preia alt ecran.
