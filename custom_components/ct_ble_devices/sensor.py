"""Sensor platform for CT BLE Devices."""
import logging
from typing import Dict

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .scanner import BLEScanner

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the CT BLE Devices sensor platform."""
    scanner: BLEScanner = hass.data[DOMAIN][entry.entry_id]
    
    # 跟踪已创建的实体（通过MAC地址）
    known_entities: Dict[str, GaitDeviceSensor] = {}

    @callback
    def _handle_device_update(device_info: Dict) -> None:
        """处理设备更新：创建新实体或更新现有实体."""
        address = device_info["address"]
        
        if address in known_entities:
            # 设备已存在，更新实体数据
            entity = known_entities[address]
            entity.update_device_data(device_info)
            _LOGGER.debug("更新设备实体: %s (地址: %s)", device_info["name"], address)
        else:
            # 新设备，创建实体
            entity = GaitDeviceSensor(hass, entry, scanner, device_info)
            known_entities[address] = entity
            async_add_entities([entity], update_before_add=True)
            _LOGGER.info("创建新设备实体: %s (地址: %s)", device_info["name"], address)
    
    # 注册实体回调
    scanner.register_entity_callback(_handle_device_update)
    
    # 为已存在的设备创建实体（如果有）
    for address, device_info in scanner.devices.items():
        if device_info.get("name", "").startswith("Gait"):
            if address not in known_entities:
                entity = GaitDeviceSensor(hass, entry, scanner, device_info)
                known_entities[address] = entity
                async_add_entities([entity], update_before_add=True)
    
    _LOGGER.info("CT BLE Devices 传感器平台已设置，已创建 %d 个设备实体", len(known_entities))


class GaitDeviceSensor(SensorEntity):
    """为每个Gait设备创建的传感器实体."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        scanner: BLEScanner,
        device_info: Dict,
    ) -> None:
        """Initialize the sensor."""
        self._hass = hass
        self._entry = entry
        self._scanner = scanner
        self._device_info = device_info
        self._address = device_info["address"]
        self._name = device_info["name"]
        
        # 设置实体属性
        self._attr_name = f"Gait Device {self._name}"
        self._attr_unique_id = f"{entry.entry_id}_{self._address}"
        self._attr_native_value = self._device_info.get("rssi", 0)
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_unit_of_measurement = "dBm"
        self._attr_icon = "mdi:bluetooth"
        self._attr_entity_registry_enabled_default = True
        
        # 设置设备信息
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._address)},
            name=self._name,
            manufacturer="Gait",
            model="BLE Device",
            connections={(dr.CONNECTION_BLUETOOTH, self._address)},
        )
        
        _LOGGER.info(
            "创建 Gait 设备传感器实体: %s (地址: %s, 唯一ID: %s)",
            self._name,
            self._address,
            self._attr_unique_id,
        )
    
    @callback
    def update_device_data(self, device_info: Dict) -> None:
        """更新设备数据."""
        self._device_info = device_info
        self._attr_native_value = device_info.get("rssi", 0)
        if self.hass and self.entity_id:
            self.async_write_ha_state()
    
    async def async_added_to_hass(self) -> None:
        """当实体被添加到 Home Assistant 时调用."""
        await super().async_added_to_hass()
        _LOGGER.info(
            "Gait 设备传感器实体已添加到 Home Assistant: entity_id=%s, unique_id=%s, 设备: %s",
            self.entity_id,
            self._attr_unique_id,
            self._name,
        )
    
    async def async_update(self) -> None:
        """Update sensor state from scanner."""
        if self._address in self._scanner.devices:
            device_info = self._scanner.devices[self._address]
            self.update_device_data(device_info)

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        attrs = {
            "mac_address": self._address,
            "device_name": self._name,
            "rssi": self._device_info.get("rssi"),
            "last_update": self._device_info.get("timestamp"),
            "source": self._device_info.get("source"),
        }
        
        # 添加制造商数据
        if self._device_info.get("manufacturer_data"):
            attrs["manufacturer_data"] = {
                f"0x{mid:04X}": data.hex() if isinstance(data, bytes) else str(data)
                for mid, data in self._device_info["manufacturer_data"].items()
            }
        
        # 添加服务数据
        if self._device_info.get("service_data"):
            attrs["service_data"] = {
                uuid: data.hex() if isinstance(data, bytes) else str(data)
                for uuid, data in self._device_info["service_data"].items()
            }
        
        # 添加服务UUID列表
        if self._device_info.get("service_uuids"):
            attrs["service_uuids"] = self._device_info["service_uuids"]
        
        if self._device_info.get("tx_power") is not None:
            attrs["tx_power"] = self._device_info["tx_power"]
        
        return attrs



