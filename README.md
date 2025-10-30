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
OCR is done on the addon which gets the Pictures per REST API and saves the result in /share/ocr/results.json. The Sensor only loads the items when the price matches. 

## Beispiel Anzeige:


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
