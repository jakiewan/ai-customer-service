package com.globalcare.domain.api;

import com.globalcare.domain.model.ApiError;
import com.globalcare.domain.service.DomainNotFoundException;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import static org.junit.jupiter.api.Assertions.assertEquals;

class GlobalExceptionHandlerTest {

    @Test
    void shouldKeepSpecificOrderNotFoundCode() {
        GlobalExceptionHandler handler = new GlobalExceptionHandler();

        ResponseEntity<ApiError> response = handler.handleNotFound(
                new DomainNotFoundException("订单不存在", "ORDER_NOT_FOUND")
        );

        assertEquals(HttpStatus.NOT_FOUND, response.getStatusCode());
        assertEquals("ORDER_NOT_FOUND", response.getBody().code());
        assertEquals(false, response.getBody().retryable());
    }
}
