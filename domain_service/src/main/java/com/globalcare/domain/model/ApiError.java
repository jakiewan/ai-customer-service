package com.globalcare.domain.model;

public record ApiError(String code, String message, boolean retryable) {
}
