"""High-precision physical, digital, and engineering unit conversion engine for SimpleUnits."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass


class UnitConversionError(Exception):
    """Raised when an unknown category/unit or non-finite numeric value is encountered."""


@dataclass(frozen=True, slots=True)
class UnitDefinition:
    """Represents a single measurable unit within a category."""

    unit_id: str
    category_id: str
    name: str
    symbol: str
    to_base_factor: float = 1.0
    to_base_fn: Callable[[float], float] | None = None
    from_base_fn: Callable[[float], float] | None = None
    aliases: tuple[str, ...] = ()

    def to_base(self, value: float) -> float:
        """Convert a value in this unit into the category's canonical SI/base unit."""
        if self.to_base_fn is not None:
            return self.to_base_fn(value)
        return value * self.to_base_factor

    def from_base(self, base_value: float) -> float:
        """Convert a value from the category's canonical base unit into this unit."""
        if self.from_base_fn is not None:
            return self.from_base_fn(base_value)
        return base_value / self.to_base_factor


@dataclass(frozen=True, slots=True)
class UnitCategory:
    """Logical grouping of related units (e.g., Length, Mass, Temperature)."""

    category_id: str
    name: str
    description: str
    default_from: str
    default_to: str
    units: tuple[UnitDefinition, ...]


@dataclass(frozen=True, slots=True)
class ConversionOutput:
    """Result of converting a numeric input from one unit to another."""

    category_id: str
    from_unit: UnitDefinition
    to_unit: UnitDefinition
    input_value: float
    output_value: float
    formatted_output: str
    formula_summary: str


def format_unit_value(value: float, sig_figs: int = 10) -> str:
    """Format a converted value cleanly without IEEE-754 floating-point noise."""
    if not math.isfinite(value):
        raise UnitConversionError("Converted value exceeds finite numerical range.")

    if abs(value) < 1e-15:
        return "0"

    clamped_figs = max(2, min(15, int(sig_figs)))
    formatted = f"{value:.{clamped_figs}g}"

    if "e" not in formatted and "E" not in formatted:
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
        return formatted

    return formatted.replace("e+", "e")


def _u(
    cat: str,
    uid: str,
    name: str,
    symbol: str,
    factor: float,
    aliases: tuple[str, ...] = (),
) -> UnitDefinition:
    """Helper to construct standard linear-factor UnitDefinition entries."""
    return UnitDefinition(
        unit_id=uid,
        category_id=cat,
        name=name,
        symbol=symbol,
        to_base_factor=factor,
        aliases=aliases,
    )


# ----------------------------------------------------------------------
# Canonical Unit Catalog (13 Categories)
# ----------------------------------------------------------------------

UNIT_CATEGORIES: tuple[UnitCategory, ...] = (
    UnitCategory(
        category_id="length",
        name="Length & Distance",
        description="Metric, imperial, astronomical, and precision engineering distances",
        default_from="m",
        default_to="ft",
        units=(
            _u("length", "nm", "Nanometer", "nm", 1e-9, ("nanometre",)),
            _u("length", "um", "Micrometer", "µm", 1e-6, ("micron", "micrometre")),
            _u("length", "mm", "Millimeter", "mm", 1e-3, ("millimetre",)),
            _u("length", "cm", "Centimeter", "cm", 1e-2, ("centimetre",)),
            _u("length", "m", "Meter", "m", 1.0, ("metre",)),
            _u("length", "km", "Kilometer", "km", 1000.0, ("kilometre",)),
            _u("length", "in", "Inch", "in", 0.0254, ("inches", '"')),
            _u("length", "ft", "Foot", "ft", 0.3048, ("feet", "'")),
            _u("length", "yd", "Yard", "yd", 0.9144, ("yards",)),
            _u("length", "mi", "Mile", "mi", 1609.344, ("miles",)),
            _u("length", "nmi", "Nautical Mile", "nmi", 1852.0, ("nautical",)),
        ),
    ),
    UnitCategory(
        category_id="mass",
        name="Mass & Weight",
        description="Metric mass, avoirdupois weights, and precious stone measurements",
        default_from="kg",
        default_to="lb",
        units=(
            _u("mass", "mg", "Milligram", "mg", 1e-6),
            _u("mass", "g", "Gram", "g", 1e-3),
            _u("mass", "kg", "Kilogram", "kg", 1.0, ("kilo",)),
            _u("mass", "t", "Metric Tonne", "t", 1000.0, ("tonne", "ton")),
            _u("mass", "ct", "Carat", "ct", 0.0002),
            _u("mass", "oz", "Ounce", "oz", 0.028349523125),
            _u("mass", "lb", "Pound", "lb", 0.45359237, ("lbs", "pounds")),
            _u("mass", "st", "Stone", "st", 6.35029318),
            _u("mass", "ton_us", "US Short Ton", "ton", 907.18474),
        ),
    ),
    UnitCategory(
        category_id="temperature",
        name="Temperature",
        description="Thermodynamic and everyday temperature scales",
        default_from="c",
        default_to="f",
        units=(
            UnitDefinition(
                unit_id="c",
                category_id="temperature",
                name="Celsius",
                symbol="°C",
                to_base_fn=lambda c: c,
                from_base_fn=lambda c: c,
                aliases=("centigrade", "degc"),
            ),
            UnitDefinition(
                unit_id="f",
                category_id="temperature",
                name="Fahrenheit",
                symbol="°F",
                to_base_fn=lambda f: (f - 32.0) * (5.0 / 9.0),
                from_base_fn=lambda c: (c * 9.0 / 5.0) + 32.0,
                aliases=("degf",),
            ),
            UnitDefinition(
                unit_id="k",
                category_id="temperature",
                name="Kelvin",
                symbol="K",
                to_base_fn=lambda k: k - 273.15,
                from_base_fn=lambda c: c + 273.15,
                aliases=("kelvins",),
            ),
            UnitDefinition(
                unit_id="r",
                category_id="temperature",
                name="Rankine",
                symbol="°R",
                to_base_fn=lambda r: (r - 491.67) * (5.0 / 9.0),
                from_base_fn=lambda c: (c + 273.15) * (9.0 / 5.0),
                aliases=("rankine",),
            ),
        ),
    ),
    UnitCategory(
        category_id="area",
        name="Area",
        description="Architectural, land surveying, and metric/imperial surface area",
        default_from="sqm",
        default_to="sqft",
        units=(
            _u("area", "sqmm", "Square Millimeter", "mm²", 1e-6),
            _u("area", "sqcm", "Square Centimeter", "cm²", 1e-4),
            _u("area", "sqm", "Square Meter", "m²", 1.0),
            _u("area", "ha", "Hectare", "ha", 10_000.0),
            _u("area", "sqkm", "Square Kilometer", "km²", 1_000_000.0),
            _u("area", "sqin", "Square Inch", "in²", 0.00064516),
            _u("area", "sqft", "Square Foot", "ft²", 0.09290304),
            _u("area", "sqyd", "Square Yard", "yd²", 0.83612736),
            _u("area", "ac", "Acre", "ac", 4046.8564224, ("acres",)),
            _u("area", "sqmi", "Square Mile", "mi²", 2_589_988.110336),
        ),
    ),
    UnitCategory(
        category_id="volume",
        name="Volume & Capacity",
        description="Metric liters, cubic measures, and US/Imperial culinary capacities",
        default_from="l",
        default_to="gal_us",
        units=(
            _u("volume", "ml", "Milliliter", "mL", 1e-3, ("cc", "cm3")),
            _u("volume", "cl", "Centiliter", "cL", 1e-2),
            _u("volume", "l", "Liter", "L", 1.0, ("litre",)),
            _u("volume", "m3", "Cubic Meter", "m³", 1000.0),
            _u("volume", "tsp", "US Teaspoon", "tsp", 0.00492892159375),
            _u("volume", "tbsp", "US Tablespoon", "tbsp", 0.01478676478125),
            _u("volume", "floz", "US Fluid Ounce", "fl oz", 0.0295735295625),
            _u("volume", "cup", "US Cup", "cup", 0.2365882365),
            _u("volume", "pt", "US Pint", "pt", 0.473176473),
            _u("volume", "qt", "US Quart", "qt", 0.946352946),
            _u("volume", "gal_us", "US Gallon", "gal", 3.785411784),
            _u("volume", "gal_uk", "Imperial Gallon", "imp gal", 4.54609),
        ),
    ),
    UnitCategory(
        category_id="speed",
        name="Speed & Velocity",
        description="Terrestrial, aviation, maritime, and aerodynamic speeds",
        default_from="kmh",
        default_to="mph",
        units=(
            _u("speed", "mps", "Meters per Second", "m/s", 1.0),
            _u("speed", "kmh", "Kilometers per Hour", "km/h", 1.0 / 3.6, ("kph",)),
            _u("speed", "mph", "Miles per Hour", "mph", 0.44704),
            _u("speed", "kn", "Knot", "kn", 1852.0 / 3600.0, ("knots", "kt")),
            _u("speed", "fps", "Feet per Second", "ft/s", 0.3048),
            _u("speed", "mach", "Mach (Sea Level)", "Mach", 340.29),
        ),
    ),
    UnitCategory(
        category_id="time",
        name="Time & Duration",
        description="High-precision intervals through calendar durations",
        default_from="h",
        default_to="min",
        units=(
            _u("time", "ns", "Nanosecond", "ns", 1e-9),
            _u("time", "us", "Microsecond", "µs", 1e-6),
            _u("time", "ms", "Millisecond", "ms", 1e-3),
            _u("time", "s", "Second", "s", 1.0, ("sec",)),
            _u("time", "min", "Minute", "min", 60.0),
            _u("time", "h", "Hour", "h", 3600.0, ("hr",)),
            _u("time", "d", "Day", "d", 86_400.0),
            _u("time", "wk", "Week", "wk", 604_800.0),
            _u("time", "mo", "Month (Gregorian Avg)", "mo", 2_629_746.0),
            _u("time", "yr", "Year (Gregorian)", "yr", 31_556_952.0),
        ),
    ),
    UnitCategory(
        category_id="data",
        name="Digital Storage",
        description="SI decimal (KB, MB, GB) and IEC binary (KiB, MiB, GiB) storage units",
        default_from="gb",
        default_to="gib",
        units=(
            _u("data", "bit", "Bit", "bit", 0.125),
            _u("data", "b", "Byte", "B", 1.0),
            _u("data", "kb", "Kilobyte (SI)", "KB", 1e3),
            _u("data", "mb", "Megabyte (SI)", "MB", 1e6),
            _u("data", "gb", "Gigabyte (SI)", "GB", 1e9),
            _u("data", "tb", "Terabyte (SI)", "TB", 1e12),
            _u("data", "pb", "Petabyte (SI)", "PB", 1e15),
            _u("data", "kib", "Kibibyte (Binary)", "KiB", float(1024)),
            _u("data", "mib", "Mebibyte (Binary)", "MiB", float(1024**2)),
            _u("data", "gib", "Gibibyte (Binary)", "GiB", float(1024**3)),
            _u("data", "tib", "Tebibyte (Binary)", "TiB", float(1024**4)),
        ),
    ),
    UnitCategory(
        category_id="bandwidth",
        name="Data Transfer Rate",
        description="Network throughput in bits/sec and file transfer speeds in bytes/sec",
        default_from="mbps",
        default_to="mbs",
        units=(
            _u("bandwidth", "bps", "Bits per Second", "bps", 1.0),
            _u("bandwidth", "kbps", "Kilobits per Second", "Kbps", 1e3),
            _u("bandwidth", "mbps", "Megabits per Second", "Mbps", 1e6),
            _u("bandwidth", "gbps", "Gigabits per Second", "Gbps", 1e9),
            _u("bandwidth", "bs", "Bytes per Second", "B/s", 8.0),
            _u("bandwidth", "kbs", "Kilobytes per Second", "KB/s", 8e3),
            _u("bandwidth", "mbs", "Megabytes per Second", "MB/s", 8e6),
            _u("bandwidth", "gbs", "Gigabytes per Second", "GB/s", 8e9),
        ),
    ),
    UnitCategory(
        category_id="pressure",
        name="Pressure",
        description="Atmospheric, hydraulic, pneumatic, and scientific pressure scales",
        default_from="bar",
        default_to="psi",
        units=(
            _u("pressure", "pa", "Pascal", "Pa", 1.0),
            _u("pressure", "kpa", "Kilopascal", "kPa", 1000.0),
            _u("pressure", "mpa", "Megapascal", "MPa", 1_000_000.0),
            _u("pressure", "mbar", "Millibar", "mbar", 100.0),
            _u("pressure", "bar", "Bar", "bar", 100_000.0),
            _u("pressure", "atm", "Standard Atmosphere", "atm", 101_325.0),
            _u("pressure", "psi", "Pounds per Square Inch", "psi", 6894.757293168),
            _u("pressure", "mmhg", "Millimeters of Mercury", "mmHg", 133.322387415, ("torr",)),
        ),
    ),
    UnitCategory(
        category_id="energy",
        name="Energy & Work",
        description="Mechanical work, nutritional calories, electrical watt-hours, and thermal energy",
        default_from="kcal",
        default_to="kj",
        units=(
            _u("energy", "j", "Joule", "J", 1.0),
            _u("energy", "kj", "Kilojoule", "kJ", 1000.0),
            _u("energy", "mj", "Megajoule", "MJ", 1_000_000.0),
            _u("energy", "cal", "Gram Calorie", "cal", 4.184),
            _u("energy", "kcal", "Kilocalorie (Food)", "kcal", 4184.0),
            _u("energy", "wh", "Watt-Hour", "Wh", 3600.0),
            _u("energy", "kwh", "Kilowatt-Hour", "kWh", 3_600_000.0),
            _u("energy", "btu", "British Thermal Unit", "BTU", 1055.05585262),
            _u("energy", "ftlbf", "Foot-Pound", "ft·lbf", 1.3558179483314),
        ),
    ),
    UnitCategory(
        category_id="power",
        name="Power",
        description="Electrical wattage, mechanical horsepower, and thermal power",
        default_from="kw",
        default_to="hp",
        units=(
            _u("power", "w", "Watt", "W", 1.0),
            _u("power", "kw", "Kilowatt", "kW", 1000.0),
            _u("power", "mw", "Megawatt", "MW", 1_000_000.0),
            _u("power", "hp", "Mechanical Horsepower", "hp", 745.69987158227),
            _u("power", "btuh", "BTU per Hour", "BTU/h", 0.29307107017),
        ),
    ),
    UnitCategory(
        category_id="angle",
        name="Plane Angle",
        description="Geometric, trigonometric, and surveying angular units",
        default_from="deg",
        default_to="rad",
        units=(
            _u("angle", "deg", "Degree", "°", 1.0, ("degree", "degrees")),
            _u("angle", "rad", "Radian", "rad", 180.0 / math.pi, ("radian", "radians")),
            _u("angle", "grad", "Gradian", "grad", 0.9, ("gon",)),
            _u("angle", "arcmin", "Arcminute", "′", 1.0 / 60.0),
            _u("angle", "arcsec", "Arcsecond", "″", 1.0 / 3600.0),
            _u("angle", "turn", "Turn / Revolution", "turn", 360.0, ("rev",)),
        ),
    ),
)


class UnitConverter:
    """Lookup and conversion engine backed by UNIT_CATEGORIES."""

    def __init__(self) -> None:
        self._categories: dict[str, UnitCategory] = {
            cat.category_id: cat for cat in UNIT_CATEGORIES
        }
        self._units_by_category: dict[str, dict[str, UnitDefinition]] = {
            cat.category_id: {u.unit_id: u for u in cat.units}
            for cat in UNIT_CATEGORIES
        }

    @property
    def categories(self) -> tuple[UnitCategory, ...]:
        """Return all registered categories in display order."""
        return UNIT_CATEGORIES

    def get_category(self, category_id: str) -> UnitCategory:
        """Return a UnitCategory by ID or raise UnitConversionError."""
        cat = self._categories.get(category_id)
        if cat is None:
            raise UnitConversionError(f"Unknown unit category '{category_id}'.")
        return cat

    def get_unit(self, category_id: str, unit_id: str) -> UnitDefinition:
        """Return a UnitDefinition within a category or raise UnitConversionError."""
        cat_units = self._units_by_category.get(category_id)
        if cat_units is None:
            raise UnitConversionError(f"Unknown unit category '{category_id}'.")
        unit = cat_units.get(unit_id)
        if unit is None:
            raise UnitConversionError(
                f"Unknown unit '{unit_id}' in category '{category_id}'."
            )
        return unit

    def convert(
        self,
        category_id: str,
        from_unit_id: str,
        to_unit_id: str,
        value: float,
        sig_figs: int = 10,
    ) -> ConversionOutput:
        """Convert `value` from `from_unit_id` to `to_unit_id` inside `category_id`."""
        if not math.isfinite(value):
            raise UnitConversionError("Input value must be a finite number.")

        src = self.get_unit(category_id, from_unit_id)
        dst = self.get_unit(category_id, to_unit_id)

        if src.unit_id == dst.unit_id:
            result = float(value)
        else:
            base_val = src.to_base(float(value))
            result = dst.from_base(base_val)

        formatted = format_unit_value(result, sig_figs=sig_figs)
        unit_ratio = format_unit_value(dst.from_base(src.to_base(1.0)), sig_figs=6)
        formula_summary = f"1 {src.symbol} = {unit_ratio} {dst.symbol}"

        return ConversionOutput(
            category_id=category_id,
            from_unit=src,
            to_unit=dst,
            input_value=float(value),
            output_value=result,
            formatted_output=formatted,
            formula_summary=formula_summary,
        )

    def convert_to_all(
        self,
        category_id: str,
        from_unit_id: str,
        value: float,
        sig_figs: int = 10,
    ) -> list[ConversionOutput]:
        """Convert `value` from `from_unit_id` into every unit in `category_id`."""
        cat = self.get_category(category_id)
        return [
            self.convert(
                category_id=category_id,
                from_unit_id=from_unit_id,
                to_unit_id=target.unit_id,
                value=value,
                sig_figs=sig_figs,
            )
            for target in cat.units
        ]

    def search_units(self, query: str) -> list[tuple[UnitCategory, UnitDefinition]]:
        """Search across all categories and units by name, symbol, or alias."""
        needle = query.strip().lower()
        if not needle:
            return []

        matches: list[tuple[UnitCategory, UnitDefinition]] = []
        for cat in UNIT_CATEGORIES:
            for unit in cat.units:
                haystack = (
                    unit.unit_id.lower(),
                    unit.name.lower(),
                    unit.symbol.lower(),
                    cat.name.lower(),
                    *(a.lower() for a in unit.aliases),
                )
                if any(needle in term for term in haystack):
                    matches.append((cat, unit))
        return matches