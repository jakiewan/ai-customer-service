package com.globalcare.domain.service;

import com.globalcare.domain.model.CaseDraftRequest;
import com.globalcare.domain.model.CaseDraftResponse;
import com.globalcare.domain.model.DeviceView;
import com.globalcare.domain.model.OrderView;
import com.globalcare.domain.model.RepairStatusView;
import com.globalcare.domain.model.ServiceCenterView;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class DomainService {

    private static final String CN_ORDER_ID = "ORD-CN-1001";
    private static final String MY_ORDER_ID = "ORD-MY-2001";
    private static final String CN_SERIAL_NUMBER = "SN-CN-1001";
    private static final String MY_SERIAL_NUMBER = "SN-MY-2001";
    private static final String STATUS_RECEIVED = "RECEIVED";
    private static final String STATUS_IN_REPAIR = "IN_REPAIR";
    private static final String CASE_DRAFT_CREATED = "DRAFT_CREATED";

    private final Map<String, OrderView> orders = new ConcurrentHashMap<>();
    private final Map<String, CaseDraftResponse> draftsByFingerprint = new ConcurrentHashMap<>();
    private final Map<String, CaseDraftResponse> draftsById = new ConcurrentHashMap<>();

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
                MY_ORDER_ID,
                "CUS-MY-001",
                "MY",
                "Xiaomi 15",
                MY_SERIAL_NUMBER,
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

    public CaseDraftResponse createCaseDraft(CaseDraftRequest request) {
        CaseDraftResponse existing = draftsByFingerprint.get(request.draftFingerprint());
        if (existing != null) {
            return new CaseDraftResponse(
                    existing.caseDraftId(),
                    existing.status(),
                    existing.conversationId(),
                    existing.draftFingerprint(),
                    true,
                    existing.createdAt(),
                    existing.payload()
            );
        }
        String draftId = UUID.randomUUID().toString();
        OffsetDateTime createdAt = OffsetDateTime.now();
        Map<String, Object> payload = Map.ofEntries(
                Map.entry("market", request.market()),
                Map.entry("language", request.language()),
                Map.entry("issue_category", request.issueCategory()),
                Map.entry("issue_summary", request.issueSummary()),
                Map.entry("suggested_resolution", request.suggestedResolution()),
                Map.entry("customer_context", request.customerContext()),
                Map.entry("policy_evidence", request.policyEvidence()),
                Map.entry("tool_trace_ids", request.toolTraceIds()),
                Map.entry("order_id", request.orderId() == null ? "" : request.orderId()),
                Map.entry("serial_number", request.serialNumber() == null ? "" : request.serialNumber()),
                Map.entry("product_model", request.productModel() == null ? "" : request.productModel())
        );
        CaseDraftResponse response = new CaseDraftResponse(
                draftId,
                CASE_DRAFT_CREATED,
                request.conversationId(),
                request.draftFingerprint(),
                false,
                createdAt,
                payload
        );
        draftsByFingerprint.put(request.draftFingerprint(), response);
        draftsById.put(draftId, response);
        return response;
    }

    public CaseDraftResponse getCaseDraft(String draftId) {
        CaseDraftResponse response = draftsById.get(draftId);
        if (response == null) {
            throw new DomainNotFoundException("Case Draft 不存在");
        }
        return response;
    }

    public List<ServiceCenterView> getServiceCenters(String market) {
        return switch (market == null ? "" : market.toUpperCase()) {
            case "CN" -> List.of(new ServiceCenterView("SC-CN-001", "北京模拟服务中心", "CN", "北京"));
            case "MY" -> List.of(new ServiceCenterView("SC-MY-001", "Kuala Lumpur Simulated Service Center", "MY", "Kuala Lumpur"));
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
