#!/usr/bin/env bash
# Build the throwaway repository that docs/demo.tape records.
#
#   scripts/demo/prepare.sh
#
# Creates /tmp/gitcommit-ai-demo (or $DEMO_DIR, which docs/demo.tape does not follow) containing:
#   repo/  a small project whose history uses scoped Conventional Commits with short
#          bodies, plus one staged change that is waiting for a message
#   bin/   a git-ai-commit shim that runs this checkout, so `git ai-commit` works
#          without installing anything
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
demo="${DEMO_DIR:-/tmp/gitcommit-ai-demo}"

rm -rf "$demo"
mkdir -p "$demo/repo" "$demo/bin"

cat > "$demo/bin/git-ai-commit" <<SHIM
#!/usr/bin/env bash
exec uv run --quiet --all-extras --project "$root" git-ai-commit "\$@"
SHIM
chmod +x "$demo/bin/git-ai-commit"

cd "$demo/repo"
git init --quiet --initial-branch=main
git config user.name "Demo Author"
git config user.email "demo@example.com"
git config commit.gpgsign false

# commit "<subject>" "<body>": commit everything in the working tree.
commit() {
  git add -A
  git commit --quiet -m "$1" -m "$2"
}

mkdir -p src/orders tests

cat > src/orders/__init__.py <<'PY'
"""Orders service."""
PY
cat > src/orders/models.py <<'PY'
from dataclasses import dataclass


@dataclass
class Order:
    id: int
    customer: str
    total: float
PY
commit "feat(models): add order dataclass" \
  "Orders are plain records, so a dataclass keeps the model small and typed."

cat > src/orders/api.py <<'PY'
from orders.models import Order


def list_orders(orders: list[Order]) -> list[Order]:
    return orders


def get_order(orders: list[Order], order_id: int) -> Order | None:
    return next((o for o in orders if o.id == order_id), None)
PY
commit "feat(api): add list_orders and get_order" \
  "Clients need to read orders before they can act on them."

cat > tests/test_api.py <<'PY'
from orders.api import get_order, list_orders
from orders.models import Order

ORDERS = [Order(1, "ada", 10.0), Order(2, "lin", 25.5)]


def test_list_orders_returns_every_order() -> None:
    assert list_orders(ORDERS) == ORDERS


def test_get_order_finds_by_id() -> None:
    assert get_order(ORDERS, 2) == ORDERS[1]
PY
commit "test(api): cover list_orders and get_order" \
  "Both functions are the public surface of the module, so pin their behavior."

cat > src/orders/cli.py <<'PY'
from orders.api import list_orders
from orders.models import Order


def main(orders: list[Order]) -> None:
    for order in list_orders(orders):
        print(f"{order.id}\t{order.customer}\t{order.total:.2f}")
PY
commit "feat(cli): add orders command" \
  "Prints one tab-separated line per order so the output can be piped."

cat > src/orders/api.py <<'PY'
from orders.models import Order


def list_orders(orders: list[Order]) -> list[Order]:
    return list(orders)


def get_order(orders: list[Order], order_id: int) -> Order | None:
    return next((o for o in orders if o.id == order_id), None)
PY
commit "fix(api): return a copy of the orders list" \
  "Callers could mutate the stored list through the returned reference."

cat > README.md <<'MD'
# orders

A tiny orders service used to demonstrate GitCommit AI.
MD
commit "docs(readme): describe the orders service" \
  "A short README tells new contributors what the project is for."

cat > src/orders/models.py <<'PY'
from dataclasses import dataclass


@dataclass(frozen=True)
class Order:
    id: int
    customer: str
    total: float
PY
commit "refactor(models): make order immutable" \
  "Orders are never edited in place, and frozen instances can be hashed."

cat > src/orders/cli.py <<'PY'
from orders.api import list_orders
from orders.models import Order


def main(orders: list[Order]) -> None:
    if not orders:
        print("no orders")
        return
    for order in list_orders(orders):
        print(f"{order.id}\t{order.customer}\t{order.total:.2f}")
PY
commit "fix(cli): print a message when there are no orders" \
  "An empty result looked like a crash because nothing was printed."

# The change waiting for a message: pagination for list_orders, with tests.
cat > src/orders/api.py <<'PY'
from orders.models import Order

DEFAULT_PAGE_SIZE = 20


def list_orders(
    orders: list[Order], page: int = 1, page_size: int = DEFAULT_PAGE_SIZE
) -> list[Order]:
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be at least 1")
    start = (page - 1) * page_size
    return orders[start : start + page_size]


def get_order(orders: list[Order], order_id: int) -> Order | None:
    return next((o for o in orders if o.id == order_id), None)
PY
cat >> tests/test_api.py <<'PY'


def test_list_orders_returns_the_requested_page() -> None:
    assert list_orders(ORDERS, page=2, page_size=1) == [ORDERS[1]]


def test_list_orders_rejects_a_page_below_one() -> None:
    import pytest

    with pytest.raises(ValueError):
        list_orders(ORDERS, page=0)
PY
git add -A

echo "Demo repository ready: $demo/repo"
