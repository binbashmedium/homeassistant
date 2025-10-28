"""Read the balance of your bank accounts via FinTS."""

from __future__ import annotations

from collections import namedtuple
from datetime import timedelta, date, datetime
import logging
from typing import Any, cast

from fints.client import FinTS3PinTanClient
from fints.models import SEPAAccount
from propcache.api import cached_property
import voluptuous as vol

from homeassistant.components.sensor import (
    PLATFORM_SCHEMA as SENSOR_PLATFORM_SCHEMA,
    SensorEntity,
)
from homeassistant.const import CONF_NAME, CONF_PIN, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

import json
import re
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(hours=1)
ICON = "mdi:currency-eur"

# ──────────────────────────────────────────────────────────────────────────────
# Receipt-Daten (direkt aus der JSON lesen; Matching NUR über Betrag)
# ──────────────────────────────────────────────────────────────────────────────

RECEIPTS_DB = Path("/share/ocr/results.json")


def _load_receipts() -> list:
    """Liest die erkannte Belegliste aus results.json."""
    if RECEIPTS_DB.exists():
        try:
            return json.loads(RECEIPTS_DB.read_text())
        except Exception as e:
            _LOGGER.error("receipt-load: %s", e)
            return []
    return []


def _find_receipt_for(amount: float) -> dict | None:
    """Sucht den Beleg nur nach Betrag (±0,05 € Toleranz)."""
    receipts = _load_receipts()
    if not receipts:
        return None

    AMOUNT_TOL = 0.05
    best = None
    best_diff = 999.0

    for r in receipts:
        rec_total = r.get("total")
        if rec_total is None:
            continue
        try:
            diff = abs(float(rec_total) - float(amount))
        except Exception:
            continue
        if diff <= AMOUNT_TOL and diff < best_diff:
            best = r
            best_diff = diff

    return best


# ──────────────────────────────────────────────────────────────────────────────
# FinTS Integration
# ──────────────────────────────────────────────────────────────────────────────

BankCredentials = namedtuple("BankCredentials", "blz login pin url product_id")

CONF_BIN = "bank_identification_number"
CONF_ACCOUNTS = "accounts"
CONF_HOLDINGS = "holdings"
CONF_ACCOUNT = "account"
CONF_PRODUCT_ID = "product_id"

ATTR_ACCOUNT = CONF_ACCOUNT
ATTR_BANK = "bank"
ATTR_ACCOUNT_TYPE = "account_type"
EXCLUDE_KEYWORDS = "exclude_keywords"

SCHEMA_ACCOUNTS = vol.Schema(
    {
        vol.Required(CONF_ACCOUNT): cv.string,
        vol.Optional(CONF_NAME, default=None): vol.Any(None, cv.string),
    }
)

PLATFORM_SCHEMA = SENSOR_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_BIN): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PIN): cv.string,
        vol.Required(CONF_URL): cv.string,
        vol.Optional(CONF_PRODUCT_ID): cv.string,
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_ACCOUNTS, default=[]): cv.ensure_list(SCHEMA_ACCOUNTS),
        vol.Optional(CONF_HOLDINGS, default=[]): cv.ensure_list(SCHEMA_ACCOUNTS),
        vol.Optional(EXCLUDE_KEYWORDS, default=[]): cv.ensure_list(cv.string),
    }
)


def setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:

    credentials = BankCredentials(
        config[CONF_BIN],
        config[CONF_USERNAME],
        config[CONF_PIN],
        config[CONF_URL],
        config.get(CONF_PRODUCT_ID, None),
    )
    fints_name = cast(str, config.get(CONF_NAME, config[CONF_BIN]))

    account_config = {acc[CONF_ACCOUNT]: acc[CONF_NAME] for acc in config[CONF_ACCOUNTS]}
    holdings_config = {acc[CONF_ACCOUNT]: acc[CONF_NAME] for acc in config[CONF_HOLDINGS]}

    client = FinTsClient(credentials, fints_name, account_config, holdings_config)
    balance_accounts, holdings_accounts = client.detect_accounts()
    entities: list[SensorEntity] = []

    for account in balance_accounts:
        if config[CONF_ACCOUNTS] and account.iban not in account_config:
            continue

        account_name = account_config.get(account.iban) or f"{fints_name} - {account.iban}"
        entities.append(FinTsAccount(client, account, account_name))
        entities.append(
            FinTsMonthlyExpensesSensor(
                client, account, fints_name, exclude_filter=config.get(EXCLUDE_KEYWORDS, [])
            )
        )

    for account in holdings_accounts:
        if config[CONF_HOLDINGS] and account.accountnumber not in holdings_config:
            continue

        account_name = holdings_config.get(account.accountnumber) or f"{fints_name} - {account.accountnumber}"
        entities.append(FinTsHoldingsAccount(client, account, account_name))

    add_entities(entities, True)


class FinTsClient:
    """Wrapper around the FinTS3PinTanClient."""

    def __init__(self, credentials: BankCredentials, name: str, account_config: dict, holdings_config: dict) -> None:
        self._credentials = credentials
        self._account_information: dict[str, dict] = {}
        self._account_information_fetched = False
        self.name = name
        self.account_config = account_config
        self.holdings_config = holdings_config

    @cached_property
    def client(self) -> FinTS3PinTanClient:
        return FinTS3PinTanClient(
            self._credentials.blz,
            self._credentials.login,
            self._credentials.pin,
            self._credentials.url,
            product_id=self._credentials.product_id,
        )

    def detect_accounts(self) -> tuple[list, list]:
        balance_accounts: list[SEPAAccount] = []
        holdings_accounts: list[SEPAAccount] = []
        accounts = self.client.get_sepa_accounts()
        for account in accounts:
            if self.is_balance_account(account):
                balance_accounts.append(account)
            elif self.is_holdings_account(account):
                holdings_accounts.append(account)
        return balance_accounts, holdings_accounts

    def get_account_information(self, iban: str) -> dict | None:
        if not self._account_information_fetched:
            self._account_information = {
                account["iban"]: account
                for account in self.client.get_information()["accounts"]
            }
            self._account_information_fetched = True
        return self._account_information.get(iban, None)

    def is_balance_account(self, account: SEPAAccount) -> bool:
        if not account.iban:
            return False
        ai = self.get_account_information(account.iban)
        if not ai:
            return False
        if (t := ai.get("type")) and 1 <= t <= 9:
            return True
        return account.iban in self.account_config

    def is_holdings_account(self, account: SEPAAccount) -> bool:
        if not account.iban:
            return False
        ai = self.get_account_information(account.iban)
        if not ai:
            return False
        if (t := ai.get("type")) and 30 <= t <= 39:
            return True
        return account.accountnumber in self.holdings_config


class FinTsAccount(SensorEntity):
    """Saldo-Sensor pro Konto."""

    def __init__(self, client: FinTsClient, account: SEPAAccount, name: str) -> None:
        self._client = client
        self._account = account
        self._attr_name = name
        self._attr_icon = ICON
        self._attr_extra_state_attributes = {
            ATTR_ACCOUNT: self._account.iban,
            ATTR_ACCOUNT_TYPE: "balance",
        }
        if self._client.name:
            self._attr_extra_state_attributes[ATTR_BANK] = self._client.name

    def update(self) -> None:
        bank = self._client.client
        try:
            balance = bank.get_balance(self._account)
            if balance is None:
                self._attr_native_value = None
                return
            self._attr_native_value = balance.amount.amount
            self._attr_native_unit_of_measurement = balance.amount.currency
        except Exception:
            self._attr_native_value = None


class FinTsMonthlyExpensesSensor(SensorEntity):
    """Monatliche Ausgaben inkl. verknüpfter Receipt-Details (per Betrag)."""

    def __init__(self, client: FinTsClient, account: SEPAAccount, name: str, exclude_filter: list[str] | None = None) -> None:
        self._client = client
        self._account = account
        self._attr_name = f"{name} Monthly Expenses"
        self._attr_icon = "mdi:cash-minus"
        self._attr_native_unit_of_measurement = "EUR"
        self._exclude_filter = exclude_filter or []
        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "account": self._account.iban,
            "excluded_keywords": self._exclude_filter,
            "transaction_count": 0,
            "transactions": [],
        }

    def update(self) -> None:
        today = date.today()
        first_day = today.replace(day=1)

        try:
            transactions = self._client.client.get_transactions(self._account, first_day, today, True)
            total = 0.0
            parsed_transactions: list[dict[str, Any]] = []

            for tx in transactions:
                data = getattr(tx, "data", None)
                if not data:
                    continue

                amount_obj = data.get("amount")
                if not amount_obj:
                    continue

                try:
                    amount = float(str(amount_obj.amount))
                except Exception:
                    continue

                currency = getattr(amount_obj, "currency", "EUR")
                purpose = (data.get("purpose") or "").strip()
                applicant_name = (data.get("applicant_name") or "").strip()

                date_val = data.get("date") or data.get("valutadate")

                # date_str wird für spätere Auswertungen aufbereitet (optional)
                if isinstance(date_val, (datetime, date)):
                    date_str = date_val.strftime("%Y-%m-%d")
                elif isinstance(date_val, str) and re.match(r"\d{4}-\d{2}-\d{2}", date_val):
                    date_str = date_val
                else:
                    m = re.search(r"(\d{2}\.\d{2})", purpose)
                    if m:
                        d, mth = m.group(1).split(".")
                        yr = today.year
                        date_str = f"{yr}-{mth}-{d}"
                    else:
                        date_str = None

                # Filter für unerwünschte Buchungen
                if any(ex.lower() in (purpose + applicant_name).lower() for ex in self._exclude_filter):
                    continue

                # Nur Ausgaben (negative Beträge)
                if amount < 0:
                    total += amount

                    # Beleg NUR über Betrag suchen
                    receipt = _find_receipt_for(abs(amount))

                    parsed_tx: dict[str, Any] = {
                        "date": str(date_val),
                        "amount": abs(amount),
                        "currency": currency,
                        "name": applicant_name or "Unbekannt",
                        "purpose": purpose[:120],
                    }

                    if receipt:
                        parsed_tx["store"] = receipt.get("store")
                        parsed_tx["items"] = receipt.get("items", [])
                        parsed_tx["file"] = receipt.get("file")
                    else:
                        parsed_tx["store"] = None
                        parsed_tx["items"] = []
                        parsed_tx["file"] = None

                    parsed_transactions.append(parsed_tx)

            self._attr_native_value = abs(total)
            self._attr_extra_state_attributes.update(
                {
                    "transaction_count": len(parsed_transactions),
                    "transactions": parsed_transactions,
                }
            )

        except Exception as e:
            _LOGGER.error("FinTS Monthly Error: %s", e)
            self._attr_native_value = None


class FinTsHoldingsAccount(SensorEntity):
    """Depot-/Holdings-Sensor."""

    def __init__(self, client: FinTsClient, account: SEPAAccount, name: str) -> None:
        self._client = client
        self._attr_name = name
        self._account = account
        self._holdings: list[Any] = []
        self._attr_icon = ICON
        self._attr_native_unit_of_measurement = "EUR"

    def update(self) -> None:
        bank = self._client.client
        self._holdings = bank.get_holdings(self._account)
        self._attr_native_value = sum(h.total_value for h in self._holdings)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            ATTR_ACCOUNT: self._account.accountnumber,
            ATTR_ACCOUNT_TYPE: "holdings",
        }
        if self._client.name:
            attributes[ATTR_BANK] = self._client.name
        for holding in self._holdings:
            attributes[f"{holding.name} total"] = holding.total_value
            attributes[f"{holding.name} pieces"] = holding.pieces
            attributes[f"{holding.name} price"] = holding.market_value
        return attributes
