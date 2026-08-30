package com.globalcare.domain.model;

public record RepairStatusView(
        String orderId,
        String serialNumber,
        String market,
        String productModel,
        String status,
        String statusLabel
) {
}
