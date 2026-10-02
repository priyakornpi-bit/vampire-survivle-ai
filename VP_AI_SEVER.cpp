#include <algorithm>
#include <cmath>
#include <iostream>
#include <sstream>
#include <string>

// [TH] รวมไลบรารี WinSock2 สำหรับจัดการ Socket บนระบบปฏิบัติการ Windows
// [EN] Include WinSock library for Windows socket programming
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")

// [TH] โครงสร้างข้อมูลสำหรับเก็บพิกัดตำแหน่ง (X, Y)
// [EN] Structure representing 2D coordinates
struct Position {
    float x;
    float y;
};

// [TH] แปลงข้อความที่ได้รับจาก Python "PX,PY|EX,EY|TX,TY" ออกมาเป็นพิกัดวัตถุ
// [EN] Parse the message string "PX,PY|EX,EY|TX,TY" into Position objects
bool parseFrame(const std::string& message, Position& player, Position& enemy, Position& target) {
    std::stringstream stream(message);
    char playerComma = 0;
    char enemyComma = 0;
    char targetComma = 0;
    char playerSeparator = 0;
    char targetSeparator = 0;
    return (stream >> player.x >> playerComma >> player.y >> playerSeparator
            >> enemy.x >> enemyComma >> enemy.y >> targetSeparator
            >> target.x >> targetComma >> target.y)
        && playerComma == ',' && enemyComma == ',' && targetComma == ','
        && playerSeparator == '|' && targetSeparator == '|';
}

// [TH] ตรวจสอบว่าพิกัดนั้นตรวจพบจริงหรือไม่ (ถ้าไม่ใช่ค่าติดลบถือว่าพบ)
// [EN] Validate if coordinate values represent a valid detected object
bool validPosition(const Position& position) {
    return position.x >= 0.0f && position.y >= 0.0f;
}

// [TH] ฟังก์ชันคำนวณทิศทางการเคลื่อนที่ตามกฎแบบ Heuristic Rules
// [EN] Calculate recommended action based on deterministic heuristic rules
std::string calculateAction(const Position& player, const Position& enemy, const Position& target) {
    const bool enemyDetected = validPosition(enemy);
    const bool targetDetected = validPosition(target);
    const float deltaX = player.x - enemy.x;
    const float deltaY = player.y - enemy.y;
    const float distance = std::sqrt(deltaX * deltaX + deltaY * deltaY);

    // [TH] กฎข้อที่ 1: หากมีศัตรูและเข้าใกล้ระยะอันตราย (<240px) ให้เดินถอยหนีทันที
    // [EN] Rule 1: Prioritize escape if an enemy is detected within danger zone (<240px)
    if (enemyDetected && distance < 240.0f) {
        const float escapeX = deltaX / distance;
        const float escapeY = deltaY / distance;
        if (std::fabs(escapeX) >= std::fabs(escapeY)) {
            return escapeX >= 0.0f ? "MOVE_RIGHT" : "MOVE_LEFT";
        }
        return escapeY >= 0.0f ? "MOVE_DOWN" : "MOVE_UP";
    }

    // [TH] กฎข้อที่ 2: หากปลอดภัย ให้เดินเข้าหาตำแหน่งเพชร/ทอง
    // [EN] Rule 2: Move towards the target pickup if environment is safe
    if (targetDetected) {
        const float targetX = target.x - player.x;
        const float targetY = target.y - player.y;
        if (std::fabs(targetX) < 15.0f && std::fabs(targetY) < 15.0f) {
            return "MOVE_NONE";
        }
        if (std::fabs(targetX) >= std::fabs(targetY)) {
            return targetX >= 0.0f ? "MOVE_RIGHT" : "MOVE_LEFT";
        }
        return targetY >= 0.0f ? "MOVE_DOWN" : "MOVE_UP";
    }

    return "MOVE_NONE";
}

int main(int argc, char* argv[]) {
    int port = 8081;
    if (argc > 1) {
        try {
            port = std::stoi(argv[1]);
        } catch (const std::exception&) {
            std::cerr << "Invalid port: " << argv[1] << std::endl;
            return 1;
        }
    }

    // [TH] เริ่มต้นการใช้งาน WinSock Library
    // [EN] Initialize WinSock DLL
    WSADATA wsaData;
    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0) {
        std::cerr << "WSAStartup failed" << std::endl;
        return 1;
    }

    // [TH] สร้าง TCP Socket
    // [EN] Create TCP Socket
    SOCKET serverSocket = socket(AF_INET, SOCK_STREAM, 0);
    if (serverSocket == INVALID_SOCKET) {
        std::cerr << "socket failed: " << WSAGetLastError() << std::endl;
        WSACleanup();
        return 1;
    }

    sockaddr_in serverAddr{};
    serverAddr.sin_family = AF_INET;
    inet_pton(AF_INET, "127.0.0.1", &serverAddr.sin_addr);
    serverAddr.sin_port = htons(static_cast<u_short>(port));

    // [TH] ผูก Socket กับ IP/Port และเปิดรอรับการเชื่อมต่อ (Listen)
    // [EN] Bind socket and listen for incoming connection
    if (bind(serverSocket, reinterpret_cast<sockaddr*>(&serverAddr), sizeof(serverAddr)) == SOCKET_ERROR ||
        listen(serverSocket, 1) == SOCKET_ERROR) {
        std::cerr << "bind/listen failed: " << WSAGetLastError() << std::endl;
        closesocket(serverSocket);
        WSACleanup();
        return 1;
    }

    std::cout << "[C++ Server] Listening on 127.0.0.1:" << port << std::endl;

    // [TH] ยืนยันการเชื่อมต่อจาก Python Client
    // [EN] Accept connection request from Python client
    SOCKET clientSocket = accept(serverSocket, nullptr, nullptr);
    if (clientSocket == INVALID_SOCKET) {
        std::cerr << "accept failed: " << WSAGetLastError() << std::endl;
        closesocket(serverSocket);
        WSACleanup();
        return 1;
    }
    std::cout << "[C++ Server] Python client connected" << std::endl;

    // [TH] ลูปการทำงานหลัก: รับพิกัด ประมวลผลคำสั่ง และส่งกลับไปยัง Python
    // [EN] Main processing loop: Receive frame coordinates, calculate action, and respond
    std::string pending;
    char buffer[4096];
    while (true) {
        const int bytesReceived = recv(clientSocket, buffer, sizeof(buffer), 0);
        if (bytesReceived <= 0) {
            break;
        }
        pending.append(buffer, bytesReceived);

        std::size_t newline = 0;
        while ((newline = pending.find('\n')) != std::string::npos) {
            const std::string frame = pending.substr(0, newline);
            pending.erase(0, newline + 1);
            Position player{};
            Position enemy{};
            Position target{};
            const std::string action = parseFrame(frame, player, enemy, target)
                ? calculateAction(player, enemy, target)
                : "MOVE_NONE";
            const std::string response = action + "\n";
            send(clientSocket, response.c_str(), static_cast<int>(response.size()), 0);
        }
    }

    closesocket(clientSocket);
    closesocket(serverSocket);
    WSACleanup();
    return 0;
}