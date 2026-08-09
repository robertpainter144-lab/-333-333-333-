package com.lab333.terminal.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

val BG       = Color(0xFF0D1117)
val BG2      = Color(0xFF161B22)
val BG3      = Color(0xFF21262D)
val BDR      = Color(0xFF30363D)
val FG       = Color(0xFFC9D1D9)
val FG2      = Color(0xFF8B949E)
val Accent   = Color(0xFF58A6FF)
val Good     = Color(0xFF3FB950)
val Bad      = Color(0xFFF85149)
val Warn     = Color(0xFFD29922)
val Purple   = Color(0xFFBC8CFF)

private val DarkColors = darkColorScheme(
    primary             = Accent,
    onPrimary           = BG,
    primaryContainer    = BG3,
    onPrimaryContainer  = FG,
    secondary           = Purple,
    onSecondary         = BG,
    tertiary            = Good,
    onTertiary          = BG,
    background          = BG,
    onBackground        = FG,
    surface             = BG2,
    onSurface           = FG,
    surfaceVariant      = BG3,
    onSurfaceVariant    = FG2,
    outline             = BDR,
    error               = Bad,
)

private val MonoTypography = Typography(
    bodyLarge   = TextStyle(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Normal, fontSize = 15.sp),
    bodyMedium  = TextStyle(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Normal, fontSize = 13.sp),
    bodySmall   = TextStyle(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Normal, fontSize = 11.sp),
    titleLarge  = TextStyle(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold,   fontSize = 20.sp),
    titleMedium = TextStyle(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold,   fontSize = 16.sp),
    titleSmall  = TextStyle(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold,   fontSize = 13.sp),
    labelLarge  = TextStyle(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Medium, fontSize = 13.sp),
    labelMedium = TextStyle(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Medium, fontSize = 11.sp),
    labelSmall  = TextStyle(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Medium, fontSize = 10.sp),
)

@Composable
fun TradingTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DarkColors,
        typography = MonoTypography,
        content = content,
    )
}
