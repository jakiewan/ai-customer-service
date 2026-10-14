package com.globalcare.domain.service;

import com.globalcare.domain.model.DeviceView;
import com.globalcare.domain.model.OrderView;
import com.globalcare.domain.model.RepairStatusView;
import com.globalcare.domain.model.ServiceCenterView;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class DomainService {

    private static final String CN_ORDER_ID = "ORD-CN-1001";
    private static final String CN_REPAIR_ORDER_ID = "ORD-CN-2001";
    private static final String CN_SERIAL_NUMBER = "SN-CN-1001";
    private static final String CN_REPAIR_SERIAL_NUMBER = "SN-CN-2001";
    private static final String STATUS_RECEIVED = "RECEIVED";
    private static final String STATUS_IN_REPAIR = "IN_REPAIR";

    private final Map<String, OrderView> orders = new ConcurrentHashMap<>();

    public DomainService() {
        addOrder(new OrderView(
                CN_ORDER_ID,
                "CUS-CN-001",
                "CN",
                "Xiaomi 15",
                CN_SERIAL_NUMBER,
                "2026-02-10",
                STATUS_RECEIVED
        ));
        addOrder(new OrderView(
                CN_REPAIR_ORDER_ID,
                "CUS-CN-002",
                "CN",
                "Xiaomi 15",
                CN_REPAIR_SERIAL_NUMBER,
                "2026-03-12",
                STATUS_IN_REPAIR
        ));
    }

    public OrderView getOrder(String orderId) {
        OrderView order = orders.get(orderId.toUpperCase());
        if (order == null) {
            throw new DomainNotFoundException("订单不存在");
        }
        return order;
    }

    public DeviceView getDevice(String serialNumber) {
        OrderView order = orders.values().stream()
                .filter(item -> item.serialNumber().equalsIgnoreCase(serialNumber))
                .findFirst()
                .orElseThrow(() -> new DomainNotFoundException("设备不存在"));
        return new DeviceView(
                order.serialNumber(),
                order.orderId(),
                order.customerId(),
                order.market(),
                order.productModel(),
                order.purchaseDate(),
                order.repairStatus()
        );
    }

    public RepairStatusView getRepairStatus(String orderId, String serialNumber) {
        OrderView order;
        if (orderId != null && !orderId.isBlank()) {
            order = getOrder(orderId);
        } else if (serialNumber != null && !serialNumber.isBlank()) {
            DeviceView device = getDevice(serialNumber);
            order = getOrder(device.orderId());
        } else {
            throw new IllegalArgumentException("订单号和设备序列号至少提供一个");
        }
        return new RepairStatusView(
                order.orderId(),
                order.serialNumber(),
                order.market(),
                order.productModel(),
                order.repairStatus(),
                statusLabel(order.repairStatus())
        );
    }

    public List<ServiceCenterView> getServiceCenters(String market) {
        return switch (market == null ? "" : market.toUpperCase()) {
            case "CN" -> List.of(new ServiceCenterView("SC-CN-001", "北京模拟服务中心", "CN", "北京"));
            default -> List.of();
        };
    }

    private void addOrder(OrderView order) {
        orders.put(order.orderId(), order);
    }

    private String statusLabel(String status) {
        return switch (status) {
            case STATUS_RECEIVED -> "已收件，等待检测";
            case STATUS_IN_REPAIR -> "维修处理中";
            default -> "状态已更新";
        };
    }
}
