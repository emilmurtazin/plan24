"""Контракт планировщика (ТЗ v2.1, раздел 8.1).

Пакет `plan24.planner` — чистый Python: без FastAPI, БД, файлов и текущего времени.
Любой «сегодня» передаётся явно (PlanConfig.today).

Числа хранятся как float64 — это нужно для побитового паритета с оригиналом
(JavaScript использует тот же IEEE-754). Планируются только целые операции,
поэтому Decimal из ТЗ здесь ничего не даёт (см. docs/DEVIATIONS.md, D-02).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class Operation:
    name: str
    norm_per_day: float          # выработка за полный рабочий день при perf = 1 (BR-1)
    min_skill: float = 1
    sequence: float = 999        # «Порядок»: одинаковый = параллельно
    per_item: float = 1          # «На изделие» (кратность)
    hold_qty: float = 0          # «Блок»: изделий, по которым операция не идёт
    hold_until: Optional[date] = None   # дата снятия удержания (BR-8, новое)
    hold_reason: str = ""


@dataclass(frozen=True)
class OrderLine:
    id: str
    project: str
    product: str
    qty: float
    due: Optional[date]
    priority: float = 999
    # допуск к позициям (BR-9): None = любой исполнитель; frozenset() = никому не разрешено
    allowed_workers: Optional[frozenset] = None


@dataclass(frozen=True)
class Worker:
    id: int
    name: str
    perf: float = 1
    skill: float = 1
    absent_days: frozenset = frozenset()      # BR-2/BR-9, новое по сравнению со скриптом


@dataclass(frozen=True)
class FactRow:
    """Строка листа «Факт». qty > 0 — выполнено; qty == 0 и plan > 0 — замороженный план."""
    date: date
    order_id: str
    project: str
    product: str
    op: str
    worker: str
    plan: float = 0
    qty: float = 0
    reason: str = ""


@dataclass(frozen=True)
class PlanConfig:
    today: date                              # первый рабочий день горизонта (BR-2)
    work_days: tuple = ()                    # рабочие дни горизонта, по возрастанию
    efficiency: float = 1.0                  # BR-1; 1.0 = поведение скрипта v3.x (D-01)
    max_workers_per_op: int = 4
    today_load_threshold: float = 0.6
    min_start_share: float = 0.5
    priority_strictness: float = 100.0       # BR-4, %
    strictness_by_level: dict = field(default_factory=dict)
    # D-04: в скрипте проход «после срока» игнорирует старт группы (нарушает BR-6). True = исправлено.
    fix_group_start_after_deadline: bool = False
    freeze_days: int = 2                     # сегодня и завтра: строки Факта с plan>0


@dataclass(frozen=True)
class PlanInput:
    order_lines: tuple
    products: dict                            # product name -> tuple[Operation, ...]
    workers: tuple                            # только доступные исполнители
    facts: tuple
    config: PlanConfig
    all_workers_perf: dict = field(default_factory=dict)  # name -> perf (в т.ч. недоступные), для отчётов
    # Частичное перепланирование: эти исполнители новых заданий не получают, но уже назначенное им
    # (строки Факта с plan>0, qty=0) учитывается как выполняемое — работа участка не пересчитывается.
    fixed_workers: frozenset = frozenset()


@dataclass(frozen=True)
class PlanWarning:
    code: str            # NO_OPERATIONS, NO_WORKERS, HORIZON_EXCEEDED, PRIORITY_HORIZON, DATA_INVALID
    text: str
    order_id: Optional[str] = None
    operation: Optional[str] = None


@dataclass(frozen=True)
class PlanItem:
    date: date
    order_id: str
    project: str
    product: str
    operation: str
    qty: int
    worker: str
    reason_code: str      # FROZEN | FREE_CAPACITY | AFTER_DEADLINE (BR-18)
    is_frozen: bool = False


@dataclass
class OrderStatus:
    order: OrderLine
    required_ops: int = 0
    done_ops: float = 0
    planned_ops: float = 0
    last_day_idx: int = -1
    no_ops: bool = False


@dataclass
class PlanResult:
    items: list
    warnings: list
    order_status: dict            # order_id -> OrderStatus
    loaded_today: list            # исполнители, не получающие заданий на сегодня (BR-12)
    new_fact_rows: list           # план-строки на сегодня/завтра, которые нужно записать в «Факт»
    work_days: tuple
