const API_KEY = "317203ed9c8ce2df206a3b5451c60bce"; // Replace with your key

const cityInput = document.querySelector("#city_input");
const searchBtn = document.querySelector("#searchBtn");
const locationBtn = document.querySelector("#locationBtn");

const currentTemp = document.querySelector("#currentTemp");
const currentStatus = document.querySelector("#currentStatus");
const currentLocation = document.querySelector("#currentLocation");
const mainIcon = document.querySelector("#mainIcon");
const humidityVal = document.querySelector("#humidityVal");
const windSpeedVal = document.querySelector("#windSpeedVal");
const sunriseTime = document.querySelector("#sunriseTime");
const sunsetTime = document.querySelector("#sunsetTime");
const fiveDayForecast = document.querySelector("#fiveDayForecast");
const hourlyContainer = document.querySelector("#hourlyContainer");

// Function to fetch weather
async function getWeatherDetails(cityName) {
    const WEATHER_API_URL = `https://api.openweathermap.org/data/2.5/forecast?q=${cityName}&units=metric&appid=${API_KEY}`;

    try {
        const response = await fetch(WEATHER_API_URL);
        const data = await response.json();

        if (data.cod !== "200") {
            alert("City not found!");
            return;
        }

        updateUI(data);
    } catch (error) {
        console.error("Error fetching weather:", error);
    }
}

function updateUI(data) {
    const current = data.list[0];
    const city = data.city;

    // Current Weather
    currentTemp.textContent = `${Math.round(current.main.temp)}°C`;
    currentStatus.textContent = current.weather[0].description;
    currentLocation.textContent = `${city.name}, ${city.country}`;
    mainIcon.src = `https://openweathermap.org/img/wn/${current.weather[0].icon}@2x.png`;

    // Highlights
    humidityVal.textContent = `${current.main.humidity}%`;
    windSpeedVal.textContent = `${current.wind.speed} km/h`;
    
    // Sunrise/Sunset conversion
    const formatTime = (timestamp) => {
        let date = new Date(timestamp * 1000);
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    };
    sunriseTime.textContent = formatTime(city.sunrise);
    sunsetTime.textContent = formatTime(city.sunset);

    // 5-Day Forecast (Filtered for 12:00 PM each day)
    fiveDayForecast.innerHTML = "";
    const uniqueForecastDays = [];
    const fiveDaysData = data.list.filter(item => {
        const date = new Date(item.dt_txt).getDate();
        if (!uniqueForecastDays.includes(date)) {
            uniqueForecastDays.push(date);
            return true;
        }
        return false;
    }).slice(1, 6);

    fiveDaysData.forEach(item => {
        const date = new Date(item.dt_txt);
        const dayName = date.toLocaleDateString('en-US', { weekday: 'short' });
        fiveDayForecast.innerHTML += `
            <div class="forecast-item">
                <span>${dayName}</span>
                <img src="https://openweathermap.org/img/wn/${item.weather[0].icon}.png" width="35">
                <strong>${Math.round(item.main.temp)}°C</strong>
            </div>
        `;
    });

    // Hourly Update (Next 8 slots)
    hourlyContainer.innerHTML = "";
    data.list.slice(0, 8).forEach(item => {
        const time = new Date(item.dt_txt).toLocaleTimeString([], { hour: '2-digit' });
        hourlyContainer.innerHTML += `
            <div class="hourly-item">
                <p style="font-size:0.7rem; margin-bottom:5px;">${time}</p>
                <img src="https://openweathermap.org/img/wn/${item.weather[0].icon}.png" width="30">
                <p><strong>${Math.round(item.main.temp)}°</strong></p>
            </div>
        `;
    });
}

// Event Listeners
searchBtn.addEventListener("click", () => {
    const cityName = cityInput.value.trim();
    if (cityName) getWeatherDetails(cityName);
});

cityInput.addEventListener("keyup", (e) => {
    if (e.key === "Enter") {
        const cityName = cityInput.value.trim();
        if (cityName) getWeatherDetails(cityName);
    }
});

// Default Load
window.addEventListener("load", () => {
    getWeatherDetails("Binangonan");
});