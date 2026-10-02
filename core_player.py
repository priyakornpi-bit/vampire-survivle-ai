import argparse
import ctypes
import json
import random
import socket
import time
from ctypes import wintypes
from pathlib import Path

import cv2
import mss
import numpy as np
import pyautogui

# [TH] กำหนดเวลาพักการตอบสนองของ PyAutoGUI ให้ทำงานได้รวดเร็วขึ้น
# [EN] Set PyAutoGUI execution pause interval for faster responses
pyautogui.PAUSE = 0.02
screen_capture = mss.MSS()


def parse_region(value):
    """
    [TH] แปลง String ขอบเขตหน้าจอเป็น Tuple (LEFT, TOP, WIDTH, HEIGHT)
    [EN] Parse screen bounding region into integers
    """
    values = tuple(int(item.strip()) for item in value.split(","))
    if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
        raise ValueError("region must be LEFT,TOP,WIDTH,HEIGHT")
    return values


def get_monitor_region(name):
    """
    [TH] ดึงตำแหน่งขอบเขตหน้าจอฝั่งซ้ายสุดผ่านระบบ Win32 API
    [EN] Query leftmost display monitor coordinates using Win32 API
    """
    if name != "left":
        raise ValueError(f"Unsupported monitor: {name}")

    class Rect(ctypes.Structure):
        _fields_ = (("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG))

    class MonitorInfo(ctypes.Structure):
        _fields_ = (("cbSize", wintypes.DWORD), ("rcMonitor", Rect),
                    ("rcWork", Rect), ("dwFlags", wintypes.DWORD))

    regions = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR,
                                       wintypes.HDC, ctypes.POINTER(Rect), wintypes.LPARAM)

    def callback(handle, _device_context, _rect, _data):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if ctypes.windll.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            monitor = info.rcMonitor
            regions.append((monitor.left, monitor.top,
                            monitor.right - monitor.left, monitor.bottom - monitor.top))
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(None, None, callback_type(callback), 0)
    if not regions:
        raise RuntimeError("No monitors were found")
    return min(regions, key=lambda region: (region[0], region[1]))


def capture_screen(region):
    """
    [TH] แคปภาพหน้าจอความเร็วสูงแล้วแปลงโครงสร้างสีให้อยู่ในรูป BGR สำหรับ OpenCV
    [EN] Capture screen region and format as a 3-channel BGR numpy array
    """
    if region is None:
        monitor = screen_capture.monitors[0]
    else:
        left, top, width, height = region
        monitor = {"left": left, "top": top, "width": width, "height": height}
    return np.array(screen_capture.grab(monitor))[:, :, :3]


def _largest_center(mask, min_area=20):
    """
    [TH] คำนวณหาพิกัดจุดศูนย์กลาง (Centroid) ของ Contour ที่ใหญ่ที่สุด
    [EN] Extract centroid from the largest valid contour in the binary mask
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = [contour for contour in contours if cv2.contourArea(contour) >= min_area]
    if not candidates:
        return None
    contour = max(candidates, key=cv2.contourArea)
    moments = cv2.moments(contour)
    if moments["m00"] == 0:
        return None
    return int(moments["m10"] / moments["m00"]), int(moments["m01"] / moments["m00"])


def detect_positions(frame):
    """
    [TH] ประมวลผลภาพ HSV เพื่อหาพิกัดตัวละคร, ศัตรูที่ใกล้ที่สุด, และเพชร/ทอง
    [EN] Process frame HSV to locate player, nearest enemy, and target pickup
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    
    # [TH] สร้าง Color Mask สำหรับวัตถุแต่ละประเภท
    # [EN] Create color ranges for each game object type
    player_mask = cv2.inRange(hsv, np.array([35, 70, 60]), np.array([85, 255, 255]))
    red_low = cv2.inRange(hsv, np.array([0, 90, 60]), np.array([12, 255, 255]))
    red_high = cv2.inRange(hsv, np.array([165, 90, 60]), np.array([180, 255, 255]))
    enemy_mask = cv2.bitwise_or(red_low, red_high)
    xp_mask = cv2.inRange(hsv, np.array([85, 70, 70]), np.array([135, 255, 255]))
    gold_mask = cv2.inRange(hsv, np.array([15, 100, 100]), np.array([40, 255, 255]))
    pickup_mask = cv2.bitwise_or(xp_mask, gold_mask)
    
    player = _largest_center(player_mask)
    
    # [TH] คัดหาศัตรูตัวที่ใกล้ตัวละครมากที่สุด
    # [EN] Filter nearest enemy relative to player position
    enemy_contours, _ = cv2.findContours(enemy_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    enemies = []
    for contour in enemy_contours:
        if cv2.contourArea(contour) < 12:
            continue
        moments = cv2.moments(contour)
        if moments["m00"]:
            enemies.append((int(moments["m10"] / moments["m00"]), int(moments["m01"] / moments["m00"])))
    enemy = None
    if player is not None and enemies:
        enemy = min(enemies, key=lambda point: (point[0] - player[0]) ** 2 + (point[1] - player[1]) ** 2)
        
    # [TH] คัดหาเพชร/ทอง ชิ้นที่อยู่ใกล้ตัวละครมากที่สุด
    # [EN] Filter nearest pickup item relative to player position
    pickups = []
    pickup_contours, _ = cv2.findContours(pickup_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in pickup_contours:
        if cv2.contourArea(contour) < 5:
            continue
        moments = cv2.moments(contour)
        if moments["m00"]:
            pickups.append((int(moments["m10"] / moments["m00"]), int(moments["m01"] / moments["m00"])))
    target = None
    if player is not None and pickups:
        target = min(pickups, key=lambda point: (point[0] - player[0]) ** 2 + (point[1] - player[1]) ** 2)
        
    return player, enemy, target


def detect_level_up_screen(frame):
    """
    [TH] ตรวจสอบสัดส่วนพิกเซลของกรอบ Level Up ว่าขึ้นบนหน้าจอหรือไม่
    [EN] Check panel mask threshold to detect Level Up selection screen
    """
    height, width = frame.shape[:2]
    center = frame[int(height * 0.08):int(height * 0.96), int(width * 0.30):int(width * 0.72)]
    hsv = cv2.cvtColor(center, cv2.COLOR_BGR2HSV)
    panel_mask = cv2.inRange(hsv, np.array([105, 25, 50]), np.array([140, 190, 190]))
    return cv2.countNonZero(panel_mask) / panel_mask.size > 0.20


class QLearningAgent:
    """
    [TH] ตัวประมวลผล Reinforcement Learning ด้วย Q-Learning Algorithm
    [EN] Reinforcement learning controller implementing Q-Learning logic
    """
    actions = ("MOVE_UP", "MOVE_DOWN", "MOVE_LEFT", "MOVE_RIGHT", "MOVE_NONE")

    def __init__(self, table_path, alpha=0.15, gamma=0.9, epsilon=0.2):
        self.table_path = Path(table_path)
        self.alpha = alpha     # Learning Rate
        self.gamma = gamma     # Discount Factor
        self.epsilon = epsilon # Exploration Rate
        self.q_table = {}
        if self.table_path.exists():
            self.q_table = json.loads(self.table_path.read_text(encoding="utf-8"))

    @staticmethod
    def _bucket(value, near=40, far=160):
        """
        [TH] แบ่งระยะห่างพิกัดออกเป็นช่วงหยาบๆ (-2 ถึง 2) เพื่อจำกัดขนาด Q-Table
        [EN] Discretize distance offsets into scalar bin indices
        """
        if value < -far:
            return -2
        if value < -near:
            return -1
        if value > far:
            return 2
        if value > near:
            return 1
        return 0

    def state(self, player, enemy, target):
        """
        [TH] สร้าง State Key จากตำแหน่งสัมพัทธ์ของศัตรูและเพชร
        [EN] Create state dictionary string representation
        """
        def relative_position(point):
            if player is None or point is None:
                return (0, 0, 0)
            dx = point[0] - player[0]
            dy = point[1] - player[1]
            return self._bucket(dx), self._bucket(dy), int((dx * dx + dy * dy) ** 0.5 // 100)

        return str(relative_position(enemy) + relative_position(target))

    def choose(self, state):
        """
        [TH] สุ่มเลือก Action (Exploration) หรือเลือก Action ที่ดีที่สุด (Exploitation)
        [EN] Choose action using epsilon-greedy exploration/exploitation strategy
        """
        values = self.q_table.setdefault(state, {action: 0.0 for action in self.actions})
        if random.random() < self.epsilon:
            return random.choice(self.actions)
        return max(self.actions, key=lambda action: values[action])

    def update(self, old_state, action, reward, new_state):
        """
        [TH] อัปเดต Q-value ในตารางด้วย Bellman Equation
        [EN] Update Q-value according to the Bellman equation
        """
        old_values = self.q_table.setdefault(old_state, {item: 0.0 for item in self.actions})
        new_values = self.q_table.setdefault(new_state, {item: 0.0 for item in self.actions})
        old_values[action] += self.alpha * (reward + self.gamma * max(new_values.values()) - old_values[action])

    @staticmethod
    def _distance(first, second):
        """
        [TH] คำนวณระยะห่างระหว่างจุดสองจุด
        [EN] Calculate Euclidean distance between two points
        """
        if first is None or second is None:
            return None
        return ((first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2) ** 0.5

    def reward(self, old_player, old_enemy, old_target, player, enemy, target):
        """
        [TH] ให้รางวัลเมื่อเดินหนีศัตรู/เก็บเพชรได้ และตัดคะแนนเมื่อเข้าใกล้ศัตรูเกินไป
        [EN] Calculate numerical reward signal based on environment spatial changes
        """
        score = 0.0
        old_enemy_distance = self._distance(old_player, old_enemy)
        enemy_distance = self._distance(player, enemy)
        if old_enemy_distance is not None and enemy_distance is not None:
            score += (old_enemy_distance - enemy_distance) * -0.002
        old_target_distance = self._distance(old_player, old_target)
        target_distance = self._distance(player, target)
        if old_target_distance is not None and target_distance is not None:
            score += (old_target_distance - target_distance) * 0.002
        if old_target is not None and target is None:
            score += 1.0  # ให้รางวัลใหญ่เมื่อเก็บเพชรสำเร็จ
        if enemy_distance is not None and enemy_distance < 120:
            score -= 1.0  # บทลงโทษเมื่อเข้าใกล้อันตรายเกินไป
        return score

    def save(self):
        """
        [TH] บันทึก Q-Table ลงไฟล์ JSON
        [EN] Save updated Q-table data to JSON file
        """
        self.table_path.write_text(json.dumps(self.q_table, indent=2), encoding="utf-8")


def send_positions(connection, player, enemy, target):
    """
    [TH] ส่งพิกัดตำแหน่งวัตถุไปยัง C++ Server ผ่าน Socket
    [EN] Stream string formatted position data over socket connection
    """
    if player is None:
        return
    enemy = enemy or (-1, -1)
    target = target or (-1, -1)
    message = f"{player[0]},{player[1]}|{enemy[0]},{enemy[1]}|{target[0]},{target[1]}\n"
    connection.sendall(message.encode("ascii"))


def read_action(connection, pending):
    """
    [TH] รับคำสั่งการตอบกลับจาก C++ Server
    [EN] Read and extract response action commands from socket stream
    """
    try:
        data = connection.recv(128)
    except socket.timeout:
        return None, pending
    if not data:
        raise ConnectionError("C++ server disconnected")
    pending += data.decode("ascii")
    if "\n" not in pending:
        return None, pending
    action, pending = pending.split("\n", 1)
    return action.strip(), pending


def control_player(action, dry_run=False, key_mode="wasd"):
    """
    [TH] ส่งสัญญาณกดปุ่มคีย์บอร์ดจริงเพื่อบังคับตัวละครในเกม
    [EN] Translate high-level action to OS keyboard input events
    """
    keys = {
        "wasd": {"MOVE_UP": "w", "MOVE_DOWN": "s", "MOVE_LEFT": "a", "MOVE_RIGHT": "d"},
        "arrows": {"MOVE_UP": "up", "MOVE_DOWN": "down", "MOVE_LEFT": "left", "MOVE_RIGHT": "right"},
    }[key_mode]
    key = keys.get(action)
    if dry_run:
        return
    for candidate in keys.values():
        pyautogui.keyUp(candidate)
    if key:
        pyautogui.keyDown(key)


def main():
    """
    [TH] จุดเริ่มต้นการทำงานของโปรแกรม สองระบบประสานการทำงาน (Vision + Socket + RL)
    [EN] Execution entry point coordinating loop cycles, socket connection, and hybrid RL control
    """
    parser = argparse.ArgumentParser(description="Vampire Survivors CV/socket client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--fps", type=float, default=20.0)
    capture_group = parser.add_mutually_exclusive_group()
    capture_group.add_argument("--region", type=parse_region, help="capture LEFT,TOP,WIDTH,HEIGHT")
    capture_group.add_argument("--monitor", choices=("left",), help="capture the entire left monitor")
    parser.add_argument("--focus", action="store_true", help="click the center of the capture region before starting")
    parser.add_argument("--keys", choices=("wasd", "arrows"), default="wasd", help="keyboard layout used by the game")
    parser.add_argument("--auto-upgrade", action="store_true", help="press Enter for the first upgrade when the Level Up panel appears")
    parser.add_argument("--rl", action="store_true", help="use and train the persistent Q-learning controller")
    parser.add_argument("--q-table", default="rl_q_table.json", help="path for the persistent Q-learning table")
    parser.add_argument("--dry-run", action="store_true", help="detect and log actions without pressing keys")
    parser.add_argument("--max-frames", type=int, default=0, help="stop after N frames; 0 means unlimited")
    args = parser.parse_args()

    capture_region = args.region or (get_monitor_region(args.monitor) if args.monitor else None)
    interval = 1.0 / max(args.fps, 1.0)
    frame_count = 0
    pending = ""
    level_up_active = False
    stats_started = time.perf_counter()

    agent = QLearningAgent(args.q_table) if args.rl else None
    previous_state = None
    previous_action = None
    previous_player = None
    previous_enemy = None
    previous_target = None

    # [TH] เชื่อมต่อไปยัง C++ Server
    # [EN] Connect to C++ Server socket
    with socket.create_connection((args.host, args.port), timeout=5.0) as connection:
        connection.settimeout(interval)
        print(f"[Python Client] Connected to {args.host}:{args.port}")
        try:
            if args.focus and capture_region:
                left, top, width, height = capture_region
                pyautogui.click(left + width // 2, top + height // 2)
                time.sleep(0.2)

            while args.max_frames == 0 or frame_count < args.max_frames:
                started = time.perf_counter()
                
                # 1. ดึงภาพและตรวจจับวัตถุ
                screenshot = capture_screen(capture_region)
                frame = screenshot
                player, enemy, target = detect_positions(frame)
                
                # 2. ส่งข้อมูลพิกัดไปยัง C++ Server และอ่านคำสั่งกลับ
                send_positions(connection, player, enemy, target)
                server_action, pending = read_action(connection, pending)
                
                # 3. ตรวจจับและจัดการหน้าต่าง Level Up
                level_up_visible = detect_level_up_screen(frame)
                if level_up_visible:
                    if args.auto_upgrade and not level_up_active:
                        if args.dry_run:
                            print("[Python Client] Auto-upgrade dry-run: would select the first option")
                        else:
                            pyautogui.press("enter")
                            print("[Python Client] Auto-upgrade: selected the first option")
                    level_up_active = True
                    control_player("MOVE_NONE", args.dry_run, args.keys)
                    frame_count += 1
                    time.sleep(max(0.0, interval - (time.perf_counter() - started)))
                    continue
                level_up_active = False

                # 4. ประมวลผลการตัดสินใจแบบ Hybrid (RL + C++ Safety Overrule)
                action = server_action
                if agent is not None:
                    current_state = agent.state(player, enemy, target)
                    if previous_state is not None and previous_action is not None:
                        reward = agent.reward(previous_player, previous_enemy, previous_target, player, enemy, target)
                        agent.update(previous_state, previous_action, reward, current_state)
                    
                    action = agent.choose(current_state)
                    
                    # [TH] Safety Overrule: หากศัตรูเข้าใกล้เกินระยะปลอดภัย (<180px) ให้ใช้คำสั่งหลบหลีกจาก C++ Server แทน
                    # [EN] Safety Overrule: Force heuristic decision from C++ server if enemy distance < 180px
                    if enemy is not None and player is not None and agent._distance(player, enemy) < 180:
                        action = server_action or "MOVE_NONE"

                    previous_state = current_state
                    previous_action = action
                    previous_player = player
                    previous_enemy = enemy
                    previous_target = target

                # 5. สั่งกดคีย์บอร์ดจริง
                if action:
                    control_player(action, args.dry_run, args.keys)
                    mode = "rl" if agent is not None else "heuristic"
                    print(f"frame={frame_count} mode={mode} player={player} enemy={enemy} target={target} action={action}")
                elif player is None or (enemy is None and target is None):
                    control_player("MOVE_NONE", args.dry_run, args.keys)

                frame_count += 1
                
                # แสดงประสิทธิภาพ FPS ทุกๆ 30 เฟรม
                if frame_count % 30 == 0:
                    elapsed = time.perf_counter() - stats_started
                    fps = frame_count / elapsed if elapsed else 0.0
                    latency_ms = (time.perf_counter() - started) * 1000.0
                    print(f"[Stats] fps={fps:.1f} loop_latency_ms={latency_ms:.1f}")

                time.sleep(max(0.0, interval - (time.perf_counter() - started)))

        except pyautogui.FailSafeException:
            print("[Python Client] Stopped by PyAutoGUI fail-safe; move the mouse away from a screen corner before restarting.")
        finally:
            if agent is not None:
                agent.save()
            try:
                control_player("MOVE_NONE", dry_run=args.dry_run, key_mode=args.keys)
            except pyautogui.FailSafeException:
                print("[Python Client] Move the mouse away from a screen corner to release movement keys safely.")


if __name__ == "__main__":
    main()