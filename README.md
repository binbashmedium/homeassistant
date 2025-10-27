# FinTS (Custom) implementierung
Basiert auf https://www.home-assistant.io/integrations/fints/ (https://github.com/home-assistant/core/tree/dev/homeassistant/components/fints), 
da jedoch die aktuelle implementierung keine product_id unterstützt wurde diese custom_component erstellt. 

## Sensoren

Erstellt werden die Sensoren:
- giro (siehe name aus accounts)
- xxxxxx_Monthly mit allen Transaktionen im laufenden Monat (1.xx. bis heute) sowie die Summe aller Ausgaben.
  Beim Summieren werden Buchungen die  "exclude_keywords" beinhalten ignoriert.

## Beispiel configuration.yaml Sensor

product_id: "6151256F3D4F9975B877BD4A2" siehe: https://community.home-assistant.io/t/fints-access-needs-product-id/322464/6

Alle Buchungen die exclude_keywords enthalten werden nicht im Monthly Sensor summiert. 

```yaml
sensor:
 - platform: fints_own
   bank_identification_number: !secret blz
   username: !secret bank_user
   pin: !secret bank_pin
   url: "https://fints.ing.de/fints/"
   product_id: "6151256F3D4F9975B877BD4A2"
   exclude_keywords:
      - Miete
      - Rundfunk
      - Telekom
   accounts:
      - account: !secret bank_iban_string
        name: "giro"
```

## OCR
Workaround
```
docker exec -it homeassistant bash
apk add --no-cache tesseract-ocr
mkdir -p /usr/share/tessdata
wget -O /usr/share/tessdata/deu.traineddata https://github.com/tesseract-ocr/tessdata_best/raw/main/deu.traineddata
wget -O /usr/share/tessdata/eng.traineddata https://github.com/tesseract-ocr/tessdata_best/raw/main/eng.traineddata
tesseract --list-langs
```


## Beispiel Anzeige:

### Upload
Copy to /config/www/community/file_upload_card/file_upload_card.js 
```
class FileUploadCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
    this.innerHTML = `
      <ha-card header="${config.title || 'Kassenzettel hochladen'}">
        <input type="file" id="fileInput" accept="image/*"><br><br>
        <button id="uploadBtn">Hochladen</button>
        <div id="status" style="margin-top:10px; color: var(--primary-text-color); white-space: pre-wrap;"></div>
      </ha-card>
    `;

    const status = this.querySelector("#status");
    const uploadBtn = this.querySelector("#uploadBtn");
    const fileInput = this.querySelector("#fileInput");

    uploadBtn.addEventListener("click", async () => {
      const file = fileInput.files[0];
      if (!file) {
        status.innerText = "Bitte eine Datei auswählen.";
        return;
      }

      status.innerText = "Lade Datei hoch ...";

      try {
        const reader = new FileReader();

        reader.onload = async () => {
          try {
            const base64Data = reader.result;

            // ✅ Der HA-native Service-Call (funktioniert in Browser + App)
            await this.hass.callService("fints_own", "upload_image", {
              filename: file.name,
              image_data: base64Data,
            });

            status.innerText = `Datei "${file.name}" erfolgreich hochgeladen.`;
          } catch (err) {
            status.innerText = `Fehler beim Upload-Service:\n${err.message || err}`;
          }
        };

        reader.onerror = (e) => {
          status.innerText = `Fehler beim Lesen der Datei:\n${e.target.error.message}`;
        };

        reader.readAsDataURL(file);
      } catch (outerErr) {
        status.innerText = `Allgemeiner Fehler:\n${outerErr.message || outerErr}`;
      }
    });
  }

  // 🔹 Home Assistant stellt "hass" automatisch beim Einbinden bereit
  set hass(hass) {
    this._hass = hass;
  }

  get hass() {
    return this._hass;
  }
}

customElements.define("file-upload-card", FileUploadCard);
```

```
type: custom:file-upload-card
title: Kassenzettel hochladen
```
### Anzeige mit flex-table-card
```
type: custom:flex-table-card
sort_by: Datum-
title: Ausgaben aktueller Monat
entities:
  include:
    - sensor.xxxxxx_monthly_expenses
columns:
  - name: Datum
    data: transactions.date
    modify: >
      let d = new Date(x);

      ('0' + d.getDate()).slice(-2) + '.' + ('0' + (d.getMonth()+1)).slice(-2) +
      '.'
  - name: Betrag
    data: transactions.amount
    suffix: " €"
    align: right
    modify: |
      parseFloat(x).toFixed(2)
  - name: Empfänger
    data: transactions.name
    modify: |
      x.replace('VISA', '')
css:
  table: "width: 100%; font-size: 14px;"
  th: "text-align: left; border-bottom: 1px solid var(--divider-color);"
  td: "padding: 4px 6px; border-bottom: 1px solid rgba(255,255,255,0.05);"

```
