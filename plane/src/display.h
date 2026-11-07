// On-board 128x64 SSD1306 OLED: tower distances + BLE state.
#pragma once

bool displayBegin();
// Redraws from the ranging table; call at a few Hz.
void displayUpdate(bool bleConnected);
