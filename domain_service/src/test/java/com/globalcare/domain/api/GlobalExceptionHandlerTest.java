package com.globalcare.domain.api;

import com.globalcare.domain.model.ApiError;
import com.globalcare.domain.service.DomainNotFoundException;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import static org.junit.jupiter.api.Assertions.assertEquals;

class GlobalExceptionHandlerTest {

    @Test
    void shouldKeepSpecificCaseDraftNotFoundCode() {
        GlobalExceptionHandler handler = new GlobalExceptionHandler();

        ResponseEntity<ApiError> response = handler.handleNotFound(
                new DomainNotFoundException("Case Draft 不存在", "CASE_DRAFT_NOT_FOUND")
        );

        assertEquals(HttpStatus.NOT_FOUND, response.getStatusCode());
        assertEquals("CASE_DRAFT_NOT_FOUND", response.getBody().code());
        assertEquals(false, response.getBody().retryable());
    }
}
