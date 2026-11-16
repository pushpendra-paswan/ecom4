// The orders list and one order's detail (items, shipment, return status).
import { apiRequest } from "../api.js";
import { formatDate, formatDateTime, formatMoney, orderStatusLabel } from "../format.js";

// Returns the refresh function main.js calls; orders only refetch after a reconnect (event is null)
export function renderOrderList(container) {
  // Step 1: title and the area for loading, the list, the empty state or an error
  const title = document.createElement("h1");
  title.textContent = "Your orders";
  const listArea = document.createElement("div");
  listArea.textContent = "Loading orders…";
  container.append(title, listArea);

  // Step 2: load the orders and show them newest first
  let latestLoad = 0;
  const loadOrders = async () => {
    const thisLoad = ++latestLoad;
    try {
      const orders = await apiRequest("GET", "/api/orders/orders");
      if (thisLoad !== latestLoad) {
        return;
      }

      // Step 2a: empty state
      if (orders.length === 0) {
        const empty = document.createElement("p");
        empty.className = "empty";
        empty.textContent = "You have no orders yet.";
        listArea.replaceChildren(empty);
        return;
      }

      // Step 2b: the API sorts by order number; the customer wants the newest first
      orders.sort((first, second) => Date.parse(second.ordered_at) - Date.parse(first.ordered_at));

      // Step 2c: one link per order: number, date, status, total and item names
      const list = document.createElement("ul");
      list.className = "item-list";
      for (const order of orders) {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.className = "item-link";
        link.href = `#/orders/${encodeURIComponent(order.order_number)}`;

        const number = document.createElement("span");
        number.className = "item-title";
        number.textContent = order.order_number;
        const status = document.createElement("span");
        status.className = `badge order-${order.status}`;
        status.textContent = orderStatusLabel(order.status);
        const details = document.createElement("span");
        details.className = "muted small";
        details.textContent = `${formatDateTime(order.ordered_at)} · ${formatMoney(order.total_amount)}`;
        const itemNames = document.createElement("span");
        itemNames.className = "small";
        itemNames.textContent = order.items.map((orderItem) => orderItem.product_name).join(", ");

        link.append(number, status, details, itemNames);
        item.append(link);
        list.append(item);
      }
      listArea.replaceChildren(list);
    } catch (error) {
      if (thisLoad !== latestLoad) {
        return;
      }
      const errorText = document.createElement("p");
      errorText.className = "error";
      errorText.textContent = `Could not load your orders: ${error.message}`;
      listArea.replaceChildren(errorText);
    }
  };
  loadOrders();

  // Step 3: ticket events do not change orders; refetch only after a reconnect
  return (event) => {
    if (event === null) {
      loadOrders();
    }
  };
}

export function renderOrderDetail(container, orderNumber) {
  // Step 1: back link, title and the area for loading, the order or an error
  const backLink = document.createElement("a");
  backLink.href = "#/orders";
  backLink.className = "back-link";
  backLink.textContent = "← All orders";
  const title = document.createElement("h1");
  title.textContent = `Order ${orderNumber}`;
  const detailArea = document.createElement("div");
  detailArea.textContent = "Loading order…";
  container.append(backLink, title, detailArea);

  // Step 2: load the order and build the page
  let latestLoad = 0;
  const loadOrder = async () => {
    const thisLoad = ++latestLoad;
    try {
      const order = await apiRequest("GET", `/api/orders/orders/${encodeURIComponent(orderNumber)}`);
      if (thisLoad !== latestLoad) {
        return;
      }
      const content = document.createElement("div");
      content.className = "stack";

      // Step 2a: summary: status, date, total, and the "Get help" button
      const summary = document.createElement("div");
      summary.className = "card";
      const status = document.createElement("span");
      status.className = `badge order-${order.status}`;
      status.textContent = orderStatusLabel(order.status);
      const summaryText = document.createElement("p");
      summaryText.textContent = `Ordered ${formatDateTime(order.ordered_at)} · Total ${formatMoney(order.total_amount)}`;
      const helpLink = document.createElement("a");
      helpLink.className = "button primary";
      helpLink.href = `#/new-ticket?order=${encodeURIComponent(order.order_number)}`;
      helpLink.textContent = "Get help with this order";
      const helpHint = document.createElement("p");
      helpHint.className = "muted small";
      helpHint.textContent = "To return an item or ask for a refund, open a ticket and tell our assistant which item.";
      summary.append(status, summaryText, helpLink, helpHint);
      content.append(summary);

      // Step 2b: items with category, quantity, price and their return status
      const itemsCard = document.createElement("div");
      itemsCard.className = "card";
      const itemsTitle = document.createElement("h2");
      itemsTitle.textContent = "Items";
      const itemList = document.createElement("ul");
      itemList.className = "plain-list";
      for (const orderItem of order.items) {
        const row = document.createElement("li");
        row.className = "order-item";
        const name = document.createElement("span");
        name.className = "item-title";
        name.textContent = orderItem.product_name;
        const facts = document.createElement("span");
        facts.className = "muted small";
        facts.textContent = `${orderItem.category.replaceAll("_", " ")} · Qty ${orderItem.quantity} · ${formatMoney(orderItem.unit_price)}`;
        row.append(name, facts);

        const returnRequest = orderItem.return_request;
        if (returnRequest && returnRequest.status === "requested") {
          const returnStatus = document.createElement("span");
          returnStatus.className = "badge return-requested";
          returnStatus.textContent = "Return requested, refund in progress";
          row.append(returnStatus);
        } else if (returnRequest && returnRequest.status === "refunded") {
          const returnStatus = document.createElement("span");
          returnStatus.className = "badge return-refunded";
          returnStatus.textContent = `Refunded ${formatMoney(returnRequest.refund_amount)}`;
          row.append(returnStatus);
        }
        itemList.append(row);
      }
      itemsCard.append(itemsTitle, itemList);
      content.append(itemsCard);

      // Step 2c: shipment, or "Not shipped yet"
      const shipmentCard = document.createElement("div");
      shipmentCard.className = "card";
      const shipmentTitle = document.createElement("h2");
      shipmentTitle.textContent = "Shipment";
      shipmentCard.append(shipmentTitle);
      const shipment = order.shipment;
      if (!shipment) {
        const notShipped = document.createElement("p");
        notShipped.textContent = "Not shipped yet";
        shipmentCard.append(notShipped);
      } else {
        const facts = document.createElement("dl");
        facts.className = "facts";
        const rows = [
          ["Carrier", shipment.carrier],
          ["Tracking number", shipment.tracking_number],
          ["Status", orderStatusLabel(shipment.status)],
          ["Shipped", formatDate(shipment.shipped_on)],
          ["Estimated delivery", formatDate(shipment.estimated_delivery)],
          ["Delivered", shipment.delivered_on ? formatDate(shipment.delivered_on) : "Not yet"],
        ];
        for (const [label, value] of rows) {
          const term = document.createElement("dt");
          term.textContent = label;
          const description = document.createElement("dd");
          description.textContent = value;
          facts.append(term, description);
        }
        shipmentCard.append(facts);
      }
      content.append(shipmentCard);
      detailArea.replaceChildren(content);
    } catch (error) {
      if (thisLoad !== latestLoad) {
        return;
      }
      const errorText = document.createElement("p");
      errorText.className = "error";
      errorText.textContent =
        error.status === 404 ? "This order was not found on your account." : `Could not load this order: ${error.message}`;
      detailArea.replaceChildren(errorText);
    }
  };
  loadOrder();

  // Step 3: ticket events do not change orders; refetch only after a reconnect
  return (event) => {
    if (event === null) {
      loadOrder();
    }
  };
}
