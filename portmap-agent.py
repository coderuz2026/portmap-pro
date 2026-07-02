#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PortMap Pro — агент сверки с реальностью.

Опрашивает свитч по SNMP (ifOperStatus + ifAlias) и печатает JSON,
который вставляется в PortMap Pro: меню объекта → «Сверка с реальностью».

Зависимости: net-snmp (snmpwalk в PATH).
  Debian/Ubuntu:  apt install snmp
  Windows:        есть в составе Net-SNMP (https://www.net-snmp.org)

Примеры:
  python3 portmap-agent.py 192.168.1.10
  python3 portmap-agent.py 192.168.1.10 -c mycommunity -o sw-core.json
  python3 portmap-agent.py 192.168.1.10 --first-port 1 --last-port 28

Работает с BDCOM, Cisco и любыми свитчами с поддержкой IF-MIB (SNMP v2c).
"""

import argparse
import json
import re
import shutil
import subprocess
import sys

OPER_STATUS = {"1": "up", "2": "down", "3": "testing",
               "4": "unknown", "5": "dormant", "6": "notPresent",
               "7": "lowerLayerDown"}

# Физические порты: ethernet-подобные ifType (учитываем через имя интерфейса)
PHYS_NAME = re.compile(
    r"(?:^|[^A-Za-z])(?:Eth|Fast|Gig|Giga|TGig|Ten|TwoGig|FiveGig|Twe|Fo|Hu)"
    r"[A-Za-z]*[- ]?(?:Ethernet)?\s*\d+(?:/\d+)*$", re.I)


def snmpwalk(host: str, community: str, oid: str, timeout: int) -> dict:
    """Возвращает {ifIndex: value} для указанного OID."""
    cmd = ["snmpwalk", "-v2c", "-c", community, "-On", "-Oq",
           "-t", str(timeout), "-r", "1", host, oid]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout * 4, check=True).stdout
    except FileNotFoundError:
        sys.exit("Ошибка: snmpwalk не найден. Установи пакет snmp (net-snmp).")
    except subprocess.CalledProcessError as e:
        sys.exit(f"SNMP-ошибка ({host}): {e.stderr.strip() or 'нет ответа — '
                 'проверь community и доступность'}")
    except subprocess.TimeoutExpired:
        sys.exit(f"Таймаут SNMP-опроса {host}")

    result = {}
    for line in out.splitlines():
        # формат -On -Oq:  .1.3.6.1.2.1.2.2.1.8.10001 1
        parts = line.strip().split(None, 1)
        if len(parts) < 1:
            continue
        idx = parts[0].rsplit(".", 1)[-1]
        result[idx] = parts[1].strip().strip('"') if len(parts) > 1 else ""
    return result


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Сбор статусов портов свитча для PortMap Pro")
    ap.add_argument("host", help="IP или hostname свитча")
    ap.add_argument("-c", "--community", default="public",
                    help="SNMP community (по умолчанию: public)")
    ap.add_argument("-o", "--output", help="Файл для JSON (иначе — stdout)")
    ap.add_argument("-t", "--timeout", type=int, default=5,
                    help="Таймаут SNMP, сек (по умолчанию: 5)")
    ap.add_argument("--first-port", type=int, default=1,
                    help="Номер порта в PortMap для первого физ. интерфейса")
    ap.add_argument("--last-port", type=int,
                    help="Ограничить число портов (напр. 28)")
    args = ap.parse_args()

    if shutil.which("snmpwalk") is None:
        sys.exit("Ошибка: snmpwalk не найден. Установи пакет snmp (net-snmp).")

    oper = snmpwalk(args.host, args.community,
                    ".1.3.6.1.2.1.2.2.1.8", args.timeout)   # ifOperStatus
    names = snmpwalk(args.host, args.community,
                     ".1.3.6.1.2.1.31.1.1.1.1", args.timeout)  # ifName
    alias = snmpwalk(args.host, args.community,
                     ".1.3.6.1.2.1.31.1.1.1.18", args.timeout)  # ifAlias

    # Отбираем физические порты в порядке ifIndex — это стабильно
    # соответствует порядку портов на морде для BDCOM/Cisco.
    phys = sorted(
        (int(i) for i, nm in names.items() if PHYS_NAME.search(nm or "")),
    )
    if not phys:  # fallback: интерфейсы, у которых вообще есть oper-статус
        phys = sorted(int(i) for i in oper)

    ports = {}
    port_no = args.first_port
    for ifindex in phys:
        if args.last_port and port_no > args.last_port:
            break
        i = str(ifindex)
        entry = {"oper": OPER_STATUS.get(oper.get(i, ""), "unknown"),
                 "ifname": names.get(i, "")}
        if alias.get(i):
            entry["desc"] = alias[i]
        ports[str(port_no)] = entry
        port_no += 1

    doc = {"app": "PortMap", "kind": "reality", "host": args.host,
           "ports": ports}
    text = json.dumps(doc, ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        up = sum(1 for p in ports.values() if p["oper"] == "up")
        print(f"OK: {len(ports)} портов ({up} up) → {args.output}")
    else:
        print(text)


if __name__ == "__main__":
    main()
