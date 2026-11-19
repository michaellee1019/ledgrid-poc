// Game state is RAM-only. Arduino/BLE may change NVS; restore the full original image.
#include <Arduino.h>
#include <NimBLEDevice.h>
#include <BleService.hpp>
#include <BoardIdentity.hpp>
#include <Protocol.hpp>
#include <PairingRules.hpp>
#include <esp_sleep.h>
#include <atomic>

using namespace scorebot;
struct Note { char bytes[kMaxWireMessageSize + 1]; };
QueueHandle_t notes;
String input;
uint32_t node;

void identity() {
    Serial.printf("READY %lu %s %u\n", node, NimBLEDevice::getAddress().toString().c_str(),
                  NimBLEDevice::getAddress().isPublic());
}

void emit(const char* prefix, const uint8_t* bytes, size_t length) {
    if (length > kMaxWireMessageSize) return;
    Note note{};
    memcpy(note.bytes, bytes, length);
    // A full queue ends this fixture's acceptance rather than hiding a loss.
    if (xQueueSend(notes, &note, 0) != pdTRUE) Serial.println("FAULT queue-full");
    (void)prefix;
}

NimBLEServer* server;
NimBLECharacteristic* uplink;
NimBLECharacteristic* pairing;
scorebot::Discovery selected;
std::atomic<uint16_t> connection{BLE_HS_CONN_HANDLE_NONE};
std::atomic<uint32_t> lastState{0};
std::atomic<bool> accepted{false};

class ServerCallbacks : public NimBLEServerCallbacks {
    void onConnect(NimBLEServer*, NimBLEConnInfo& info) override {
        connection = info.getConnHandle();
        Serial.printf("PEER %s %u\n", info.getAddress().toString().c_str(), info.getAddress().isPublic());
    }
    void onDisconnect(NimBLEServer*, NimBLEConnInfo&, int) override {
        connection = BLE_HS_CONN_HANDLE_NONE;
        accepted = false;
        Serial.println("LOST");
    }
} serverCallbacks;

class DownlinkCallbacks : public NimBLECharacteristicCallbacks {
    void onWrite(NimBLECharacteristic* characteristic, NimBLEConnInfo& info) override {
        const auto address = info.getAddress();
        if (!address.isPublic() || static_cast<uint64_t>(address) != TEST_LEADER_MAC) {
            Serial.printf("REJECT transport %s %u\n", address.toString().c_str(), address.isPublic());
            return;
        }
        const auto value = characteristic->getValue();
        if (value.length() > kMaxWireMessageSize) return;
        accepted = true;
        lastState = millis();
        emit("RX", value.data(), value.length());
    }
} downlinkCallbacks;

void refreshAdvertising() {
    const auto bytes = scorebot::encodeDiscovery(selected);
    pairing->setValue(bytes.data(), bytes.size());
    auto advertising = NimBLEDevice::getAdvertising();
    advertising->stop();
    NimBLEAdvertisementData primary;
    primary.setFlags(BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP);
    primary.setManufacturerData(std::string(reinterpret_cast<const char*>(bytes.data()), bytes.size()));
    advertising->enableScanResponse(true);
    if (!advertising->setAdvertisementData(primary) || !advertising->start()) {
        Serial.println("FAULT advertising-start");
    }
}

void setupRadio() {
    const String name = String("Scorebot-Bench-") + String(node, HEX);
    NimBLEDevice::init(name.c_str());
    NimBLEDevice::setMTU(185);
    server = NimBLEDevice::createServer();
    server->setCallbacks(&serverCallbacks);
    server->advertiseOnDisconnect(true);
    auto service = server->createService(kGameServiceUuid);
    pairing = service->createCharacteristic(kPairingUuid, NIMBLE_PROPERTY::READ, 22);
    selected.node = node;
    selected.role = BoardRole::Player_Red;
    service->createCharacteristic(kIdentityUuid, NIMBLE_PROPERTY::READ, 4)->setValue(node);
    service->createCharacteristic(kProtocolUuid, NIMBLE_PROPERTY::READ, 2)->setValue(kWireProtocolVersion);
    uplink = service->createCharacteristic(kUplinkUuid, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY,
                                         kMaxWireMessageSize);
    auto downlink = service->createCharacteristic(kDownlinkUuid, NIMBLE_PROPERTY::WRITE, kMaxWireMessageSize);
    downlink->setCallbacks(&downlinkCallbacks);
    if (!service->start()) { Serial.println("FAULT service-start"); return; }
    refreshAdvertising();
}

void serviceRadio() {}
void command(const String& line) {
    if (line.startsWith("P ") || line.startsWith("C ")) {
        if (line.length() != 46 || connection != BLE_HS_CONN_HANDLE_NONE) {
            Serial.println("FAULT pairing-command");
            return;
        }
        scorebot::DiscoveryPacket bytes{};
        for (size_t i = 0; i < bytes.size(); ++i) {
            const String hex = line.substring(2 + 2 * i, 4 + 2 * i);
            if (!isxdigit(hex[0]) || !isxdigit(hex[1])) {
                Serial.println("FAULT pairing-hex");
                return;
            }
            bytes[i] = strtoul(hex.c_str(), nullptr, 16);
        }
        scorebot::Discovery authority;
        if (!scorebot::decodeDiscovery(bytes.data(), bytes.size(), authority) || !authority.authority()) {
            Serial.println("FAULT pairing-authority");
            return;
        }
        selected = authority;
        selected.node = node;
        selected.kind = scorebot::DeviceKind::Player;
        selected.role = BoardRole::Player_Red;
        if (line[0] == 'C') {
            selected.kind = scorebot::DeviceKind::Leaderboard;
            selected.role = BoardRole::Leader;
        }
        selected.flags = scorebot::Selected;
        selected.occupied = 0;
        accepted = false;
        refreshAdvertising();
        Serial.println("PAIRED");
    } else if (line.startsWith("N ")) {
        const String wire = line.substring(2);
        if (!accepted || millis() - lastState > 15000 || wire.length() > kMaxWireMessageSize) {
            Serial.println("FAULT no-current-authority");
            return;
        }
        uplink->setValue(wire.c_str());
        Serial.println(uplink->notify() ? "NOTIFY_OK" : "FAULT notify");
    } else if (line == "D" && connection != BLE_HS_CONN_HANDLE_NONE) {
        server->disconnect(connection);
    } else if (line == "S") {
        const String sleep = String("{\"type\":\"status\",\"protocol\":5,\"fromNodeId\":") + node + ",\"state\":\"sleep\"}";
        uplink->setValue(sleep.c_str());
        uplink->notify();
        delay(400);
        esp_sleep_enable_timer_wakeup(3000000ULL);
        esp_deep_sleep_start();
    }
}

void setup() {
#if ARDUINO_USB_MODE && ARDUINO_USB_CDC_ON_BOOT
    const bool buffers = Serial.setTxBufferSize(4096) == 4096 && Serial.setRxBufferSize(1024) == 1024;
    Serial.setTxTimeoutMs(1000);
#else
    const bool buffers = true;
#endif
    Serial.begin(115200);
    Serial.setTimeout(20);
    if (!buffers) { Serial.println("FAULT usb-buffers"); return; }
    notes = xQueueCreate(8, sizeof(Note));
    if (!notes) { Serial.println("FAULT allocation"); return; }
    node = boardIdFromEfuseMac(ESP.getEfuseMac());
    setupRadio();
    identity();
}

void loop() {
    if (!notes) { delay(100); return; }
    serviceRadio();
    while (Serial.available()) {
        const char value = Serial.read();
        if (value == '\n') {
            if (input == "?") identity();
            else command(input);
            input = "";
        }
        else if (input.length() < kMaxWireMessageSize + 2) input += value;
        else { input = ""; Serial.println("FAULT command-size"); }
    }
    Note note{};
    while (notes && xQueueReceive(notes, &note, 0) == pdTRUE) {
        String line = String("RX ") + note.bytes + "\n";
        size_t written = 0;
        const uint32_t deadline = millis() + 1000;
        while (written < line.length() && static_cast<int32_t>(deadline - millis()) > 0) {
            written += Serial.write(reinterpret_cast<const uint8_t*>(line.c_str()) + written,
                                    line.length() - written);
            if (written < line.length()) delay(1);
        }
        if (written != line.length()) Serial.println("\nFAULT usb-write");
    }
    delay(5);
}
