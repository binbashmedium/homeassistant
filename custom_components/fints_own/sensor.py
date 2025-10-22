"""Read the balance of your bank accounts via FinTS."""

from __future__ import annotations

from collections import namedtuple
from datetime import timedelta
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
from datetime import date, timedelta

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(hours=1)

ICON = "mdi:currency-eur"

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
        vol.Optional(CONF_PRODUCT_ID): cv.string,          #  ← hinzugefügt
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
    """Set up the sensors.

    Login to the bank and get a list of existing accounts. Create a
    sensor for each account.
    """
    credentials = BankCredentials(config[CONF_BIN],
                                  config[CONF_USERNAME],
                                  config[CONF_PIN],
                                  config[CONF_URL],
                                  config.get(CONF_PRODUCT_ID, None)
                                  )
    fints_name = cast(str, config.get(CONF_NAME, config[CONF_BIN]))

    account_config = {
        acc[CONF_ACCOUNT]: acc[CONF_NAME] for acc in config[CONF_ACCOUNTS]
    }

    holdings_config = {
        acc[CONF_ACCOUNT]: acc[CONF_NAME] for acc in config[CONF_HOLDINGS]
    }

    client = FinTsClient(credentials, fints_name, account_config, holdings_config)
    balance_accounts, holdings_accounts = client.detect_accounts()
    accounts: list[SensorEntity] = []

    for account in balance_accounts:
        if config[CONF_ACCOUNTS] and account.iban not in account_config:
            _LOGGER.warning("Skipping account %s for bank %s", account.iban, fints_name)
            continue

        if not (account_name := account_config.get(account.iban)):
            account_name = f"{fints_name} - {account.iban}"
        accounts.append(FinTsAccount(client, account, account_name))
        _LOGGER.info("Creating account %s for bank %s", account.iban, fints_name)
        expense_sensor = FinTsMonthlyExpensesSensor(
            client,
            account,
            fints_name,
            exclude_filter=config.get(EXCLUDE_KEYWORDS, []),
        )
        accounts.append(expense_sensor)
        _LOGGER.info(">>> Added monthly expense sensor for %s", account.iban)

    for account in holdings_accounts:
        if config[CONF_HOLDINGS] and account.accountnumber not in holdings_config:
            _LOGGER.warning(
                "Skipping holdings %s for bank %s", account.accountnumber, fints_name
            )
            continue

        account_name = holdings_config.get(account.accountnumber)
        if not account_name:
            account_name = f"{fints_name} - {account.accountnumber}"
        accounts.append(FinTsHoldingsAccount(client, account, account_name))
        _LOGGER.warning(
            "Creating holdings %s for bank %s", account.accountnumber, fints_name
        )

    # Log all sensor names before adding them to Home Assistant

    for sensor in accounts:
        if hasattr(sensor, "_account"):
            _LOGGER.info(">>> Created sensor entity: %s (IBAN: %s)", sensor.name, sensor._account.iban)
        else:
            _LOGGER.info(">>> Created sensor entity: %s", sensor.name)

    _LOGGER.info(">>> Adding %d FinTS sensor entities", len(accounts))
    add_entities(accounts, True)


class FinTsClient:
    """Wrapper around the FinTS3PinTanClient.

    Use this class as Context Manager to get the FinTS3Client object.
    """

    def __init__(
            self,
            credentials: BankCredentials,
            name: str,
            account_config: dict,
            holdings_config: dict,
    ) -> None:
        """Initialize a FinTsClient."""
        self._credentials = credentials
        self._account_information: dict[str, dict] = {}
        self._account_information_fetched = False
        self.name = name
        self.account_config = account_config
        self.holdings_config = holdings_config

    @cached_property
    def client(self) -> FinTS3PinTanClient:
        """Get the FinTS client object.

        The FinTS library persists the current dialog with the bank
        and stores bank capabilities. So caching the client is beneficial.
        """

        return FinTS3PinTanClient(self._credentials.blz,
                                  self._credentials.login,
                                  self._credentials.pin,
                                  self._credentials.url,
                                  product_id=self._credentials.product_id,
                                  )

    def get_account_information(self, iban: str) -> dict | None:
        """Get a dictionary of account IBANs as key and account information as value."""

        if not self._account_information_fetched:
            self._account_information = {
                account["iban"]: account
                for account in self.client.get_information()["accounts"]
            }
            self._account_information_fetched = True

        return self._account_information.get(iban, None)

    def is_balance_account(self, account: SEPAAccount) -> bool:
        """Determine if the given account is of type balance account."""
        if not account.iban:
            return False

        account_information = self.get_account_information(account.iban)
        if not account_information:
            return False

        if account_type := account_information.get("type"):
            return 1 <= account_type <= 9

        if (
                account_information["iban"] in self.account_config
                or account_information["account_number"] in self.account_config
        ):
            return True

        return False

    def is_holdings_account(self, account: SEPAAccount) -> bool:
        """Determine if the given account of type holdings account."""
        if not account.iban:
            return False

        account_information = self.get_account_information(account.iban)
        if not account_information:
            return False

        if account_type := account_information.get("type"):
            return 30 <= account_type <= 39

        if (
                account_information["iban"] in self.holdings_config
                or account_information["account_number"] in self.holdings_config
        ):
            return True

        return False

    def detect_accounts(self) -> tuple[list, list]:
        """Identify the accounts of the bank."""

        balance_accounts = []
        holdings_accounts = []
        accounts = self.client.get_sepa_accounts()
        for account in accounts:
            if self.is_balance_account(account):
                balance_accounts.append(account)
            elif self.is_holdings_account(account):
                holdings_accounts.append(account)

            else:
                _LOGGER.warning(
                    "Could not determine type of account %s from %s",
                    account.iban,
                    self.client.user_id,
                )

        return balance_accounts, holdings_accounts


class FinTsAccount(SensorEntity):
    """Sensor for a FinTS balance account.

    A balance account contains an amount of money (=balance). The amount may
    also be negative.
    """

    def __init__(self, client: FinTsClient, account, name: str) -> None:
        """Initialize a FinTs balance account."""
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
        """Get the current balance and currency for the account."""
        bank = self._client.client
        _LOGGER.info(">>> Updating account %s", self._account.iban)
        try:
            balance = bank.get_balance(self._account)
            if balance is None:
                _LOGGER.error(">>> No balance returned for %s", self._account.iban)
                self._attr_native_value = None
                return

            self._attr_native_value = balance.amount.amount
            self._attr_native_unit_of_measurement = balance.amount.currency
            _LOGGER.info(
                ">>> Balance for %s: %.2f %s",
                self._account.iban,
                balance.amount.amount,
                balance.amount.currency,
            )

        except Exception as e:
            import traceback
            _LOGGER.error(">>> Error updating %s: %s", self._account.iban, e)
            _LOGGER.error(traceback.format_exc())
            self._attr_native_value = None


from datetime import date
from homeassistant.components.sensor import SensorEntity
import logging

_LOGGER = logging.getLogger(__name__)


class FinTsMonthlyExpensesSensor(SensorEntity):
    """Sensor für monatliche Ausgaben über FinTS."""

    def __init__(self, client, account, name: str, exclude_filter: list[str] | None = None) -> None:
        """Initialisierung."""
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
        """Hole und berechne die monatlichen Ausgaben."""
        today = date.today()
        first_day = today.replace(day=1)  # immer dynamisch: 1. des aktuellen Monats

        try:
            transactions = self._client.client.get_transactions(self._account, first_day, today, include_pending=True)
            total = 0.0
            parsed_transactions = []

            for tx in transactions:
                data = getattr(tx, "data", None)
                if not data:
                    continue

                amount_obj = data.get("amount")
                if not amount_obj:
                    continue

                # Betrag und Währung
                try:
                    amount = float(str(amount_obj.amount))
                except Exception:
                    continue
                currency = getattr(amount_obj, "currency", "EUR")

                # Metadaten
                purpose = (data.get("purpose") or "").strip()
                name = (data.get("applicant_name") or "").strip()
                from datetime import datetime
                import re

                # Versuch 1: direktes Feld
                date_val = data.get("date") or data.get("valutadate")

                # Versuch 2: String → Datumsformat normalisieren
                if isinstance(date_val, (datetime, date)):
                    date_str = date_val.strftime("%Y-%m-%d")
                elif isinstance(date_val, str) and re.match(r"\d{4}-\d{2}-\d{2}", date_val):
                    date_str = date_val
                else:
                 # Versuch 3: Datum aus purpose extrahieren (z. B. „KAUFUMSATZ14.10“)
                    match = re.search(r"(\d{2}\.\d{2})", purpose)
                    if match:
                       day, month = match.group(1).split(".")
                       year = today.year
                       date_str = f"{year}-{month}-{day}"
                    else:
                       date_str = None

                # Filter: Ignorierte Buchungen
                if any(ex.lower() in (purpose + name).lower() for ex in self._exclude_filter):
                    continue

                # Nur Ausgaben (negative Beträge)
                if amount < 0:
                    total += amount
                    parsed_transactions.append({
                        "date": str(date_val),
                        "amount": abs(amount),
                        "currency": currency,
                        "name": name or "Unbekannt",
                        "purpose": purpose[:120],
                    })

            # Ergebnisse in Sensor schreiben
            self._attr_native_value = abs(total)
            self._attr_extra_state_attributes.update({
                "transaction_count": len(parsed_transactions),
                "transactions": parsed_transactions,
            })

            _LOGGER.info(
                ">>> FinTS: %d Ausgaben für %s (%.2f EUR gesamt)",
                len(parsed_transactions),
                self._account.iban,
                abs(total),
            )

        except Exception as e:
            import traceback
            _LOGGER.error(">>> Fehler bei Ausgaben-Ermittlung für %s: %s", self._account.iban, e)
            _LOGGER.debug(traceback.format_exc())
            self._attr_native_value = None



class FinTsHoldingsAccount(SensorEntity):
    """Sensor for a FinTS holdings account.

    A holdings account does not contain money but rather some financial
    instruments, e.g. stocks.
    """

    def __init__(self, client: FinTsClient, account, name: str) -> None:
        """Initialize a FinTs holdings account."""
        self._client = client
        self._attr_name = name
        self._account = account
        self._holdings: list[Any] = []
        self._attr_icon = ICON
        self._attr_native_unit_of_measurement = "EUR"

    def update(self) -> None:
        """Get the current holdings for the account."""
        bank = self._client.client
        self._holdings = bank.get_holdings(self._account)
        self._attr_native_value = sum(h.total_value for h in self._holdings)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Additional attributes of the sensor.

        Lists each holding of the account with the current value.
        """
        attributes = {
            ATTR_ACCOUNT: self._account.accountnumber,
            ATTR_ACCOUNT_TYPE: "holdings",
        }
        if self._client.name:
            attributes[ATTR_BANK] = self._client.name
        for holding in self._holdings:
            total_name = f"{holding.name} total"
            attributes[total_name] = holding.total_value
            pieces_name = f"{holding.name} pieces"
            attributes[pieces_name] = holding.pieces
            price_name = f"{holding.name} price"
            attributes[price_name] = holding.market_value

        return attributes
